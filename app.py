import hashlib
import io
import json
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import date

import PIL.Image
import streamlit as st
from google import genai
from google.genai import types

from weather import build_prompt_context, get_weather, recommend_items

# ---------------------------------------------------------------------------
# 설정값 (바꿀 일이 생기면 여기만 고치면 됩니다)
# ---------------------------------------------------------------------------
DB_PATH = "modu_fit.db"
CATEGORIES = ["상의", "하의", "아우터"]
DEFAULT_CITY = "Seoul"
DEFAULT_TEMP = 18  # 날씨 조회 실패 시 임시로 쓰는 기온

# 앞에서부터 순서대로 시도합니다. 한 모델이 503(과부하)이거나 429(한도 초과)이면 다음 모델로 넘어갑니다.
# 존재하지 않는 모델 이름(404)도 자동으로 건너뛰니, 목록이 틀려도 앱은 죽지 않습니다.
# (사용 가능한 모델은 client.models.list()로 확인)
MODEL_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]
MAX_RETRIES = 2  # 모델 하나당 시도 횟수 (503일 때만 1번 더 시도)

st.set_page_config(page_title="moduFit", page_icon=None, layout="wide")

st.markdown("""
<style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-weight: 300;
    }
    input, textarea, select {
        font-weight: 300 !important;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wardrobe (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                category   TEXT NOT NULL,
                temp_min   INTEGER,
                temp_max   INTEGER,
                wear_count INTEGER DEFAULT 0,
                last_worn  TEXT,
                image      BLOB
            )
        """)


def seed_if_empty():
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM wardrobe").fetchone()[0]
    if count > 0:
        return
    initial_items = [
        ("회색 전면프린팅 후드티", "상의", 10, 18),
        ("베이지 코튼팬츠", "하의", 15, 25),
        ("생지 데님", "하의", 15, 25),
        ("차콜 롱슬리브", "상의", 15, 25),
        ("체크 후드 자켓", "아우터", 10, 20),
        ("검은색 해링턴 자켓", "아우터", 10, 20),
        ("회색 전면프린팅 반팔티", "상의", 15, 25),
        ("검은색 전면프린팅 반팔티", "상의", 15, 25),
        ("검은색 코튼팬츠", "하의", 15, 25),
        ("회색 니트", "상의", 10, 18),
        ("자색 니트", "상의", 10, 18),
    ]
    for name, category, tmin, tmax in initial_items:
        register_clothing(name, category, tmin, tmax)


def register_clothing(name, category, temp_min, temp_max, image_bytes=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wardrobe (name, category, temp_min, temp_max, wear_count, last_worn, image)
               VALUES (?, ?, ?, ?, 0, NULL, ?)""",
            (name, category, int(temp_min), int(temp_max), image_bytes),
        )


def update_clothing(item_id, name, category, temp_min, temp_max):
    with get_conn() as conn:
        conn.execute(
            "UPDATE wardrobe SET name=?, category=?, temp_min=?, temp_max=? WHERE id=?",
            (name, category, int(temp_min), int(temp_max), item_id),
        )


def delete_clothing(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM wardrobe WHERE id=?", (item_id,))


def record_worn(item_ids):
    """코디를 입었을 때 착용 횟수와 마지막 착용일을 갱신"""
    ids = [i for i in item_ids if i is not None]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with get_conn() as conn:
        conn.execute(
            f"UPDATE wardrobe SET wear_count = wear_count + 1, last_worn = ? WHERE id IN ({placeholders})",
            [date.today().isoformat(), *ids],
        )


def fetch_wardrobe():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, name, category, temp_min, temp_max, wear_count, last_worn, image
               FROM wardrobe ORDER BY id"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 날씨 (실제 조회와 소지품 규칙은 weather.py에 있습니다)
# ---------------------------------------------------------------------------
def get_current_weather(city):
    """(날씨 dict 또는 None, 경고메시지) 반환. 실패하면 None과 함께 이유를 돌려줍니다."""
    try:
        api_key = st.secrets["WEATHER_API_KEY"]
    except Exception:
        return None, "WEATHER_API_KEY가 secrets에 없어 기본 기온을 사용했습니다."

    weather = get_weather(api_key, city=city)
    if weather is None:
        return None, "날씨를 불러오지 못해 기본 기온을 사용했습니다. API 키, 도시 이름, 인터넷 연결을 확인하세요."
    return weather, None


# ---------------------------------------------------------------------------
# Gemini 공통 호출 (JSON 응답 + 재시도 + 모델 대체)
# ---------------------------------------------------------------------------
def parse_json_text(text):
    """혹시 코드블록(```)이 섞여 와도 벗겨내고 JSON으로 파싱"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def call_gemini_json(contents):
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(response_mime_type="application/json")

    last_error = None
    for model in MODEL_CANDIDATES:
        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                return parse_json_text(response.text)
            except Exception as e:
                last_error = e
                msg = str(e)
                # 503(서버 과부하)만 같은 모델로 잠깐 쉬었다가 다시 시도합니다.
                # 429(한도 초과)나 404(없는 모델)는 재시도해도 소용없으니 바로 다음 모델로 갑니다.
                overloaded = "503" in msg or "UNAVAILABLE" in msg
                if overloaded and attempt < MAX_RETRIES - 1:
                    time.sleep(1 + attempt + random.random())  # 1~2초 쉬고 재시도
                    continue
                break  # 다음 모델로
    raise last_error


# ---------------------------------------------------------------------------
# AI 기능
# ---------------------------------------------------------------------------
def analyze_image_with_ai(image_bytes):
    default = {"name": "신규 의류", "category": "상의", "temp_min": 15, "temp_max": 25}
    try:
        image = PIL.Image.open(io.BytesIO(image_bytes))
        prompt = """
        당신은 스마트 의류 분류 AI입니다. 업로드된 옷 사진을 보고 다음 4가지 정보를 정확하게 추출해주세요.
        반드시 아래 JSON 형식으로만 응답하세요.
        {
            "name": "의류 품목명 (예: 흰색 반바지, 체크셔츠, 후드티, 청바지, 검은색 슬랙스 등)",
            "category": "상의, 하의, 아우터 중 정확히 하나만 선택",
            "temp_min": 이 옷을 입기 적절한 최저 기온 (정수),
            "temp_max": 이 옷을 입기 적절한 최고 기온 (정수)
        }
        """
        data = call_gemini_json([image, prompt])

        category = data.get("category", "상의")
        if category not in CATEGORIES:
            category = "상의"
        temp_min = int(float(data.get("temp_min", 10)))
        temp_max = int(float(data.get("temp_max", 25)))
        if temp_min > temp_max:
            temp_min, temp_max = temp_max, temp_min

        return {
            "name": str(data.get("name") or "신규 의류"),
            "category": category,
            "temp_min": temp_min,
            "temp_max": temp_max,
        }
    except Exception as e:
        st.error(f"AI 분석 오류 발생: {e}")
        return default


def fallback_pick(items, category, temp, require_fit=False):
    """기온에 맞고, 덜 입은(착용 횟수 적고 오래 안 입은) 옷을 해당 카테고리에서 고름"""
    pool = [i for i in items if i["category"] == category]
    fitting = [i for i in pool if i["temp_min"] <= temp <= i["temp_max"]]
    if require_fit:
        pool = fitting
    elif fitting:
        pool = fitting
    if not pool:
        return None
    random.shuffle(pool)  # 동점일 때 매번 같은 옷만 나오지 않도록
    return min(pool, key=lambda i: (i["wear_count"] or 0, i["last_worn"] or ""))


def ai_recommend_outfit(city, current_temp, items, weather_context=""):
    if not items:
        return {"outer": None, "top": None, "bottom": None, "reason": "등록된 옷이 없습니다."}

    by_id = {i["id"]: i for i in items}
    summary = [
        {
            "id": i["id"], "name": i["name"], "category": i["category"],
            "temp_min": i["temp_min"], "temp_max": i["temp_max"],
            "wear_count": i["wear_count"], "last_worn": i["last_worn"],
        }
        for i in items
    ]

    prompt = f"""
    오늘 날짜는 {date.today().isoformat()}이고, 현재 {city}의 기온은 {current_temp}℃ 입니다.
    {weather_context}
    아래 옷장 목록 중에서 현재 기온에 가장 잘 어울리고 스타일 조합이 조화로운 '아우터', '상의', '하의'를 각각 하나씩 골라주세요.
    - 상의와 하의는 반드시 해당 카테고리의 id 중에서 골라야 합니다.
    - 아우터는 날씨상 불필요하면 null로 답하세요.
    - 최근에 입은 옷(last_worn이 가까운 날짜)이나 착용 횟수(wear_count)가 많은 옷은 가급적 피하고, 안 입은 옷을 우선하세요.

    옷장 목록:
    {json.dumps(summary, ensure_ascii=False)}

    반드시 아래 JSON 형식으로만 응답하세요.
    {{
        "outer_id": 선택된 아우터의 id (불필요하면 null),
        "top_id": 선택된 상의의 id,
        "bottom_id": 선택된 하의의 id,
        "reason": "이 코디를 추천하는 센스 있는 이유 (한국어 한 문장)"
    }}
    """

    notes = []
    ai_result = None
    try:
        ai_result = call_gemini_json([prompt])
        if not isinstance(ai_result, dict):
            raise ValueError("AI 응답이 JSON 객체가 아닙니다.")
    except Exception as e:
        ai_result = None
        notes.append(f"(AI 추천에 실패해 기온 맞춤 대체 코디를 보여드려요. 사유: {e})")

    def resolve(key, category):
        """AI가 준 id가 실제로 존재하고 카테고리도 맞을 때만 인정"""
        item = by_id.get(ai_result.get(key)) if ai_result else None
        return item if item and item["category"] == category else None

    if ai_result is None:
        outer = fallback_pick(items, "아우터", current_temp, require_fit=True)
        top = fallback_pick(items, "상의", current_temp)
        bottom = fallback_pick(items, "하의", current_temp)
        reason = "오늘 기온에 맞고 최근에 덜 입은 옷으로 골랐어요."
    else:
        outer = resolve("outer_id", "아우터")
        top = resolve("top_id", "상의")
        bottom = resolve("bottom_id", "하의")
        reason = ai_result.get("reason") or "오늘 날씨에 딱 어울리는 코디입니다!"
        if top is None:
            top = fallback_pick(items, "상의", current_temp)
            notes.append("(AI가 올바른 상의를 고르지 못해 대체했어요.)")
        if bottom is None:
            bottom = fallback_pick(items, "하의", current_temp)
            notes.append("(AI가 올바른 하의를 고르지 못해 대체했어요.)")

    return {"outer": outer, "top": top, "bottom": bottom, "reason": " ".join([reason, *notes])}


# ---------------------------------------------------------------------------
# UI 헬퍼
# ---------------------------------------------------------------------------
def render_outfit_card(label, item, empty_text):
    st.markdown(f"##### {label}")
    if item is None:
        st.write(empty_text)
        return
    st.write(item["name"])
    if item["image"]:
        st.image(item["image"], width=200)


def page_recommend(city):
    st.subheader("AI 센스 코디 추천")
    st.write("실시간 날씨와 옷장 목록을 Gemini AI가 분석하여 가장 어울리는 코디를 골라드립니다.")

    if st.button("AI 코디 추천받기"):
        weather, warning = get_current_weather(city)
        if weather:
            current_temp = round(weather["temp"])
            bring = recommend_items(weather)  # 규칙 기반이라 Gemini 호출이 필요 없음
            weather_context = build_prompt_context(weather, bring)
        else:
            current_temp, bring, weather_context = DEFAULT_TEMP, [], ""

        items = fetch_wardrobe()
        with st.spinner("Gemini AI가 오늘의 날씨와 옷장을 분석하여 코디를 조합하는 중입니다..."):
            result = ai_recommend_outfit(city, current_temp, items, weather_context)
        # 버튼 클릭 이후 다시 실행돼도 결과가 유지되도록 session_state에 저장
        st.session_state.outfit = {
            "city": city, "temp": current_temp, "weather": weather, "bring": bring,
            "warning": warning, "result": result, "recorded": False,
        }

    outfit = st.session_state.get("outfit")
    if not outfit:
        return

    result = outfit["result"]
    weather = outfit.get("weather")
    bring = outfit.get("bring", [])

    if outfit["warning"]:
        st.warning(outfit["warning"])

    temp_text = f"**{outfit['temp']}℃**"
    if weather:
        temp_text += f" ({weather['description']}, 체감 {weather['feels_like']:.0f}℃)"
    st.info(f"현재 {outfit['city']} 기온: {temp_text}\n\n💡 **AI 스타일 코멘트**: {result['reason']}")

    if weather:
        if bring:
            st.markdown("##### 오늘 챙기면 좋은 소지품")
            for b in bring:
                st.write(f"- **{b['item']}** — {b['reason']}")
        else:
            st.caption("오늘은 특별히 챙길 소지품이 없어요.")

    col1, col2, col3 = st.columns(3)
    with col1:
        render_outfit_card("추천 아우터", result["outer"], "아우터 불필요")
    with col2:
        render_outfit_card("추천 상의", result["top"], "조건에 맞는 상의 없음")
    with col3:
        render_outfit_card("추천 하의", result["bottom"], "조건에 맞는 하의 없음")

    if outfit["recorded"]:
        st.success("착용 기록이 저장되었습니다. 다음 추천부터 반영돼요.")
    elif st.button("이 코디로 입을래요 (착용 기록 저장)"):
        record_worn([
            result["outer"]["id"] if result["outer"] else None,
            result["top"]["id"] if result["top"] else None,
            result["bottom"]["id"] if result["bottom"] else None,
        ])
        st.session_state.outfit["recorded"] = True
        st.rerun()


def page_register():
    st.subheader("새로운 의류 추가하기 (Gemini AI 자동 분석)")

    tab_cam, tab_upload = st.tabs(["카메라 촬영", "사진 업로드"])
    with tab_cam:
        captured = st.camera_input("의류 사진 촬영")
    with tab_upload:
        uploaded = st.file_uploader("의류 사진 선택", type=["png", "jpg", "jpeg", "webp"])

    source = captured or uploaded
    image_bytes = source.getvalue() if source is not None else None

    # 사진이 바뀌면 이전 분석 결과를 초기화
    img_hash = hashlib.md5(image_bytes).hexdigest() if image_bytes else None
    if st.session_state.get("analyzed_hash") != img_hash:
        st.session_state.analyzed_data = None
        st.session_state.analyzed_hash = img_hash

    if image_bytes:
        st.image(image_bytes, width=300)
        if st.button("AI 자동 분석 요청"):
            with st.spinner("AI가 사진을 분석하는 중입니다..."):
                st.session_state.analyzed_data = analyze_image_with_ai(image_bytes)
            analyzed = st.session_state.analyzed_data
            st.success(f"분석 완료: '{analyzed['name']}' ({analyzed['category']})")

    analyzed = st.session_state.get("analyzed_data")
    default_name = analyzed["name"] if analyzed else ""
    default_cat_idx = CATEGORIES.index(analyzed["category"]) if analyzed else 0
    default_min = analyzed["temp_min"] if analyzed else 10
    default_max = analyzed["temp_max"] if analyzed else 25

    with st.form("add_form"):
        name = st.text_input("의류 이름", value=default_name)
        category = st.selectbox("카테고리", CATEGORIES, index=default_cat_idx)
        temp_min = st.number_input("최저 기온 (℃)", value=int(default_min), step=1)
        temp_max = st.number_input("최고 기온 (℃)", value=int(default_max), step=1)
        submitted = st.form_submit_button("옷장에 추가하기")

    if submitted:
        if not name.strip():
            st.error("의류 이름을 입력해주세요.")
        elif temp_min > temp_max:
            st.error("최저 기온은 최고 기온보다 클 수 없습니다.")
        else:
            # AI 분석 여부와 상관없이, 사진이 있으면 항상 함께 저장
            register_clothing(name.strip(), category, temp_min, temp_max, image_bytes)
            st.success(f"'{name.strip()}'이(가) 옷장에 추가되었습니다.")
            st.session_state.analyzed_data = None


def page_inventory():
    st.subheader("등록된 의류 목록")
    items = fetch_wardrobe()
    if not items:
        st.write("등록된 의류가 없습니다.")
        return

    for item in items:
        col_a, col_b, col_c = st.columns([1, 3, 1])
        with col_a:
            if item["image"]:
                st.image(item["image"], width=100)
            else:
                st.write("이미지 없음")
        with col_b:
            st.write(f"**[{item['category']}] {item['name']}**")
            st.write(f"적정 기온: {item['temp_min']}℃ ~ {item['temp_max']}℃")
            last = item["last_worn"] or "기록 없음"
            st.caption(f"착용 {item['wear_count'] or 0}회 · 마지막 착용: {last}")

            with st.expander("수정"):
                with st.form(f"edit_{item['id']}"):
                    e_name = st.text_input("이름", value=item["name"])
                    e_cat = st.selectbox("카테고리", CATEGORIES, index=CATEGORIES.index(item["category"]))
                    e_min = st.number_input("최저 기온 (℃)", value=int(item["temp_min"]), step=1)
                    e_max = st.number_input("최고 기온 (℃)", value=int(item["temp_max"]), step=1)
                    if st.form_submit_button("저장"):
                        if not e_name.strip() or e_min > e_max:
                            st.error("이름이 비었거나 기온 범위가 올바르지 않습니다.")
                        else:
                            update_clothing(item["id"], e_name.strip(), e_cat, e_min, e_max)
                            st.rerun()
        with col_c:
            if st.session_state.get("confirm_delete") == item["id"]:
                st.warning("정말 삭제할까요?")
                if st.button("삭제 확인", key=f"del_ok_{item['id']}"):
                    delete_clothing(item["id"])
                    st.session_state.confirm_delete = None
                    st.rerun()
                if st.button("취소", key=f"del_no_{item['id']}"):
                    st.session_state.confirm_delete = None
                    st.rerun()
            elif st.button("삭제", key=f"del_{item['id']}"):
                st.session_state.confirm_delete = item["id"]
                st.rerun()
        st.divider()


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    init_db()
    seed_if_empty()

    st.title("moduFit")
    st.markdown("스마트 옷장 및 코디 추천 시스템")

    menu = st.sidebar.selectbox("메뉴 선택", ["오늘의 코디 추천", "의류 등록 및 관리", "옷장 인벤토리 조회"])
    city = st.sidebar.text_input("도시 (영문)", value=DEFAULT_CITY).strip() or DEFAULT_CITY

    if menu == "오늘의 코디 추천":
        page_recommend(city)
    elif menu == "의류 등록 및 관리":
        page_register()
    else:
        page_inventory()


if __name__ == "__main__":
    main()