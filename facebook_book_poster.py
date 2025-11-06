import os
import json
import requests
import feedparser
from datetime import datetime, timedelta, timezone
import time
import random
import subprocess

# ======= ตั้งค่าเพจ/แบรนด์ =======
BRAND = "สรุปหนังสืออ่านง่าย – ใช้ได้จริงในชีวิต"

# ลิงก์ Affiliate (ใส่ของ Shopee/Lazada/อื่น ๆ)
AFF_LINKS = [
    "https://shopee.co.th/xxxxxx"  # <- แก้เป็นลิงก์ของคุณ
]

# เวลาที่จะตั้งโพสต์ของ "วันพรุ่งนี้" (เวลาไทย)
POST_TIMES = ["07:00", "12:00", "20:00"]

# RSS แหล่ง “หนังสือ/สรุป/บทความความรู้” (ปรับเพิ่มได้)
BOOK_FEEDS = [
    "https://www.se-ed.com/rss/newproducts.aspx",
    "https://www.naiin.com/rss/newbook",
    "https://www.mebmarket.com/feeds",
]

PAGE_ID = os.getenv("FB_PAGE_ID")
PAGE_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")

# Telegram แจ้งเตือน (ฟรี)
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

POSTED_FILE = "posted_books.json"


# -------------- Utilities --------------
def tg_notify(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": text},
            timeout=20,
        )
    except Exception:
        pass


def load_posted():
    if not os.path.exists(POSTED_FILE):
        return []
    try:
        return json.load(open(POSTED_FILE, "r", encoding="utf-8"))
    except Exception:
        return []


def save_posted(data):
    json.dump(data, open(POSTED_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# -------------- LLM (Ollama / Qwen) --------------
def summarize_with_ollama(text: str) -> str | None:
    """
    เรียก Ollama รัน qwen2.5:7b-instruct (ฟรีบน GitHub Actions)
    """
    prompt = f"""
คุณคือผู้สรุปหนังสือสำหรับเพจ "{BRAND}"
สรุป 4–7 บรรทัด ภาษากลาง อ่านเข้าใจง่าย ไม่ clickbait
ระบุ "แก่นหลัก" และ "สิ่งที่ผู้อ่านเอาไปใช้ได้ทันที"
ห้ามคัดลอกยาว ๆ จากต้นฉบับ ให้เรียบเรียงใหม่ด้วยคำของคุณ

เนื้อหา:
{text}
"""
    result = subprocess.run(
        ["ollama", "run", "qwen2.5:7b-instruct"],
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=240,
    )
    out = result.stdout.decode("utf-8").strip()
    return out if out else None


# -------------- Facebook --------------
def schedule_fb_post(caption: str, publish_dt_utc: datetime):
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
    data = {
        "message": caption,
        "access_token": PAGE_TOKEN,
        "published": "false",
        "scheduled_publish_time": int(publish_dt_utc.timestamp()),
    }
    r = requests.post(url, data=data, timeout=40)
    return (r.status_code == 200), r.text


# -------------- Main --------------
def main():
    assert PAGE_ID and PAGE_TOKEN, "ตั้ง FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN ใน Secrets ก่อน"

    posted = load_posted()
    posted_links = {p["link"] for p in posted}

    # 1) ดึง “รายการหนังสือใหม่/บทคัดย่อ” 3 ชิ้นล่าสุด (อายุไม่เกิน 3 วัน)
    items = []
    now_utc = datetime.now(timezone.utc)

    for rss in BOOK_FEEDS:
        feed = feedparser.parse(rss)
        for e in feed.entries:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            summary = (e.get("summary") or e.get("description") or "").strip()
            pub = e.get("published_parsed")

            if not title or not link or not pub:
                continue
            if link in posted_links:
                continue

            published = datetime(*pub[:6], tzinfo=timezone.utc)
            if (now_utc - published) > timedelta(days=3):
                continue

            items.append((title, link, summary))

    # เอามาเท่ากับจำนวนช่วงเวลาที่จะโพสต์
    items = items[: len(POST_TIMES)]
    if not items:
        tg_notify("⚠ ไม่มีรายการหนังสือใหม่พอสำหรับโพสต์พรุ่งนี้")
        print("No items")
        return

    tomorrow = now_utc + timedelta(days=1)
    new_logs = []

    for i, (title, link, summary) in enumerate(items):
        # 2) สรุปด้วย Qwen
        review = summarize_with_ollama(f"{title}\n\n{summary}\n\nที่มา: {link}")
        if not review:
            continue

        # 3) ใส่ Affiliate (ต่อท้ายโพสต์)
        aff = ""
        if AFF_LINKS:
            aff = "\n\n📚 ซื้อ/ดูรายละเอียดเพิ่มเติม\n" + "\n".join([f"👉 {u}" for u in AFF_LINKS])

        caption = f"""{BRAND} 📚

{review}

ที่มา: {link}{aff}"""

        # 4) แปลงเวลาไทย → UTC (พรุ่งนี้)
        thai_dt = datetime.strptime(f"{tomorrow.date()} {POST_TIMES[i]}", "%Y-%m-%d %H:%M")
        publish_utc = thai_dt.replace(tzinfo=timezone.utc) - timedelta(hours=7)

        # 5) ตั้งโพสต์บน Facebook
        ok, raw = schedule_fb_post(caption, publish_utc)
        status = "scheduled" if ok else "failed"

        new_logs.append({
            "title": title,
            "link": link,
            "scheduled_time_th": thai_dt.isoformat(),
            "scheduled_time_utc": publish_utc.isoformat(),
            "status": status,
            "raw": raw,
        })

        tg_notify(f"✅ ตั้งโพสต์พรุ่งนี้: {title} – {POST_TIMES[i]}")
        time.sleep(random.randint(2, 4))

    posted.extend(new_logs)
    save_posted(posted)
    print("Done")


if __name__ == "__main__":
    main()
