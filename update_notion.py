import os
import re
import time
import hashlib
from datetime import datetime, date, timezone
from dateutil import parser as dtparser

import requests
import feedparser

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["DATABASE_ID"].replace("-", "")

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# ---- RSS 소스(필요하면 늘리기) ----
RSS_FEEDS = {
    "한겨레": "https://www.hani.co.kr/rss/",
    "경향신문": "https://www.khan.co.kr/rss/rssdata/total_news.xml",
    "연합뉴스": "https://www.yna.co.kr/rss/all.xml",
}

# ---- 키워드(최종: B안 + 발전비정규직 포함) ----
KEYWORDS = {
    "기후": [
        "기후","탄소","온실가스","탄소중립","기후위기","기후재난","폭염","홍수","미세먼지",
        "환경","생태","오염","배출권","탈탄소","기후정의"
    ],
    "에너지": [
        "에너지","전력","전기요금","발전소","석탄","LNG","원전","태양광","풍력","재생에너지",
        "수소","송전망","계통","전력망","해상풍력","ESS","에너지전환","정의로운 전환"
    ],
    "노동": [
        "노동","노조","파업","임금","교섭","해고","비정규직","하청","용역","산재",
        "중대재해","안전","고용","일자리","근로","직접고용","정규직",
        "발전비정규직","발전하청","발전하청노동자","발전소 비정규직"
    ],
    "공공": [
        "공공","공기업","공공기관","지자체","시청","군청","도청","행정","정책","예산","조례",
        "위탁","민영화","공공서비스","공무원","공공성","공공부문"
    ],
    "공공갈등": [
        "갈등","공공갈등","사회적 갈등","민원","반발","대립","충돌","분쟁","집단민원",
        "협의체","주민설명회","공청회","숙의","중재","조정","협상",
        "주민수용성","수용성","주민동의","주민투표",
        "NIMBY","님비","LULU","기피시설",
        "이해관계자","이해당사자","거버넌스","상생"
    ],
    "경남": [
        "경남","경상남도","창원","진주","통영","거제","사천","김해","양산","밀양","함안",
        "창녕","고성","남해","하동","산청","함양","거창","합천","의령"
    ]
}

TOPIC_CATS = {"기후","에너지","노동","공공","공공갈등"}

def categorize(title: str, summary: str = ""):
    text = f"{title or ''} {summary or ''}".lower()
    cats = []
    for cat, words in KEYWORDS.items():
        for w in words:
            if w.lower() in text:
                cats.append(cat)
                break
    return cats

def should_keep(title: str, summary: str = ""):
    cats = categorize(title, summary)
    has_topic = any(c in TOPIC_CATS for c in cats)
    has_gyeongnam = "경남" in cats
    return (has_topic and has_gyeongnam), cats

def notion_query_by_dupkey(dupkey: str) -> bool:
    """이미 같은 중복키(url)가 있으면 True"""
    url = f"{NOTION_API}/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "중복키",
            "rich_text": {"equals": dupkey}
        },
        "page_size": 1
    }
    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return len(data.get("results", [])) > 0

def notion_create_page(title: str, link: str, media: str, article_date: str | None, summary: str, categories: list[str]):
    url = f"{NOTION_API}/pages"

    props = {
        "제목": {"title": [{"text": {"content": title[:200]}}]},
        "링크": {"url": link},
        "매체": {"select": {"name": media}},
        "요약": {"rich_text": [{"text": {"content": (summary or "")[:2000]}}]},
        "카테고리": {"multi_select": [{"name": c} for c in categories]},
        "수집일": {"date": {"start": str(date.today())}},
        "중복키": {"rich_text": [{"text": {"content": link}}]},
    }

    if article_date:
        props["날짜"] = {"date": {"start": article_date}}

    payload = {"parent": {"database_id": DATABASE_ID}, "properties": props}

    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_entry_date(entry) -> str | None:
    # RSS마다 다름: published / updated / 없음
    for key in ["published", "updated"]:
        if key in entry and entry[key]:
            try:
                dt = dtparser.parse(entry[key])
                # Notion Date는 날짜만 넣어도 충분
                return dt.date().isoformat()
            except Exception:
                pass
    return None

def main():
    inserted = 0
    checked = 0

    for media, feed_url in RSS_FEEDS.items():
        feed = feedparser.parse(feed_url)

        for e in feed.entries[:50]:  # 너무 많으면 줄이기
            title = clean_html(getattr(e, "title", "")).strip()
            link = getattr(e, "link", "").strip()
            summary = clean_html(getattr(e, "summary", "") or getattr(e, "description", ""))

            if not title or not link:
                continue

            checked += 1

            keep, cats = should_keep(title, summary)
            if not keep:
                continue

            # 중복 방지
            if notion_query_by_dupkey(link):
                continue

            article_date = parse_entry_date(e)

            # B안이므로 '경남'이 항상 포함되지만, 혹시 빠지면 보정
            if "경남" not in cats:
                cats.append("경남")

            notion_create_page(
                title=title,
                link=link,
                media=media,
                article_date=article_date,
                summary=summary,
                categories=cats
            )
            inserted += 1
            time.sleep(0.2)  # 노션 API 예의

    print(f"checked={checked}, inserted={inserted}")

if __name__ == "__main__":
    main()
