import io
import json
import random
import requests
import sqlite3
import streamlit as st
import PIL.Image
from google import genai

st.set_page_config(
    page_title="moduFit",
    page_icon=None,
    layout="wide"
)

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


def init_db():
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wardrobe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            temp_min INTEGER,
            temp_max INTEGER,
            wear_count INTEGER DEFAULT 0,
            last_worn TEXT,
            image BLOB
        )
    """)
    conn.commit()
    conn.close()


def register_clothing(name, category, temp_min, temp_max, image_bytes=None):
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wardrobe (name, category, temp_min, temp_max, wear_count, last_worn, image)
        VALUES (?, ?, ?, ?, 0, '2026-10-09', ?)
    """, (name, category, temp_min, temp_max, image_bytes))
    conn.commit()
    conn.close()


def get_current_weather(use_api, manual_temp):
    if not use_api:
        return manual_temp

    api_key = "ccbbadf63161257553c8cbbd8762e92c"
    city = "Seoul"
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"

    try:
        response = requests.get(url, timeout=3)
        data = response.json()
        return round(data["main"]["temp"])
    except:
        return manual_temp


def analyze_image_with_ai(image_bytes):
    """
    Gemini API (gemini-3.5-flash)를 활용하여 안정적으로 의류 사진을 분석하는 함수
    """
    try:
        client = genai.Client(api_key="AQ.Ab8RN6Lzf4_FF5STzHqVXNfM1rQLAEm4cUPBJvsNLo4Dg0Cy6g")
        image = PIL.Image.open(io.BytesIO(image_bytes))

        prompt = """
        당신은 스마트 의류 분류 AI입니다. 업로드된 옷 사진을 보고 다음 4가지 정보를 순서대로 정확하게 추출해주세요.
        반드시 JSON 형식으로만 응답하며, 마크다운(```json 등)이나 다른 설명은 절대 포함하지 마세요.
        {
            "name": "의류 품목명 (예: 흰색 반바지, 체크셔츠, 후드티, 청바지, 검은색 슬랙스 등)",
            "category": "상의, 하의, 아우터 중 정확히 하나만 선택",
            "temp_min": 이 옷을 입기 적절한 최저 기온 (숫자만),
            "temp_max": 이 옷을 입기 적절한 최고 기온 (숫자만)
        }
        """

        # 안정적인 gemini-3.5-flash 모델 사용
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=[image, prompt]
        )

        text_response = response.text.strip()

        if text_response.startswith("```"):
            lines = text_response.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text_response = "\n".join(lines).strip()

        parsed_data = json.loads(text_response)

        cat = parsed_data.get("category", "상의")
        if cat not in ["상의", "하의", "아우터"]:
            cat = "상의"

        return {
            "name": parsed_data.get("name", "신규 의류"),
            "category": cat,
            "temp_min": int(parsed_data.get("temp_min", 10)),
            "temp_max": int(parsed_data.get("temp_max", 25))
        }
    except Exception as e:
        st.warning("서버 트래픽이 많아 분석이 지연되었습니다. 기본값으로 설정됩니다. 다시 시도해 주세요.")
        return {"name": "신규 의류", "category": "하의", "temp_min": 15, "temp_max": 25}


def recommend_outfit_from_db(current_temp):
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, category, temp_min, temp_max, wear_count, image FROM wardrobe")
    rows = cursor.fetchall()
    conn.close()

    suitable_items = [
        {"id": row[0], "name": row[1], "category": row[2], "temp_min": row[3], "temp_max": row[4], "wear_count": row[5],
         "image": row[6]}
        for row in rows
        if row[3] <= current_temp <= row[4]
    ]

    if not suitable_items:
        return {"temp": current_temp, "outer": None, "top": None, "bottom": None}

    suitable_outers = [item for item in suitable_items if item["category"] == "아우터"]
    suitable_tops = [item for item in suitable_items if item["category"] == "상의"]
    suitable_bottoms = [item for item in suitable_items if item["category"] == "하의"]

    outer = random.choice(suitable_outers) if suitable_outers else None
    top = random.choice(suitable_tops) if suitable_tops else None
    bottom = random.choice(suitable_bottoms) if suitable_bottoms else None

    return {
        "temp": current_temp,
        "outer": outer,
        "top": top,
        "bottom": bottom
    }


if __name__ == "__main__":
    init_db()

    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM wardrobe")
    count = cursor.fetchone()[0]
    conn.close()

    if count == 0:
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
            ("자색 니트", "상의", 10, 18)
        ]
        for item in initial_items:
            register_clothing(item[0], item[1], item[2], item[3])

    st.title("moduFit")
    st.markdown("스마트 옷장 및 코디 추천 시스템")

    menu = st.sidebar.selectbox("메뉴 선택", ["오늘의 코디 추천", "의류 등록 및 관리", "옷장 인벤토리 조회"])

    if menu == "오늘의 코디 추천":
        st.subheader("실시간 날씨 연동 코디 매칭")

        c1, c2 = st.columns(2)
        with c1:
            use_api = st.checkbox("실시간 날씨 API 연동 사용", value=True)
        with c2:
            manual_temp = st.slider("수동 기온 설정 (℃)", -10, 35, 18)

        if st.button("코디 추천받기"):
            current_temp = get_current_weather(use_api, manual_temp)
            result = recommend_outfit_from_db(current_temp)

            st.write(f"현재 기온: {result['temp']}℃")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("##### 추천 아우터")
                if result["outer"]:
                    st.write(result["outer"]["name"])
                    if result["outer"]["image"]:
                        st.image(result["outer"]["image"], width=200)
                else:
                    st.write("아우터 불필요")

            with col2:
                st.markdown("##### 추천 상의")
                if result["top"]:
                    st.write(result["top"]["name"])
                    if result["top"]["image"]:
                        st.image(result["top"]["image"], width=200)
                else:
                    st.write("조건에 맞는 상의 없음")

            with col3:
                st.markdown("##### 추천 하의")
                if result["bottom"]:
                    st.write(result["bottom"]["name"])
                    if result["bottom"]["image"]:
                        st.image(result["bottom"]["image"], width=200)
                else:
                    st.write("조건에 맞는 하의 없음")

    elif menu == "의류 등록 및 관리":
        st.subheader("새로운 의류 추가하기 (Gemini AI 자동 분석)")

        captured_image = st.camera_input("의류 사진 촬영")

        if "ai_analyzed" not in st.session_state:
            st.session_state.ai_analyzed = False
            st.session_state.analyzed_data = None
            st.session_state.img_bytes = None

        if captured_image is not None:
            image_bytes = captured_image.getvalue()
            st.image(image_bytes, width=300)

            if st.button("AI 자동 분석 요청"):
                with st.spinner("AI가 사진을 분석하는 중입니다..."):
                    analyzed = analyze_image_with_ai(image_bytes)
                    st.session_state.ai_analyzed = True
                    st.session_state.analyzed_data = analyzed
                    st.session_state.img_bytes = image_bytes
                st.success(f"분석 완료: '{analyzed['name']}' ({analyzed['category']})")

        default_name = st.session_state.analyzed_data["name"] if st.session_state.ai_analyzed else ""

        categories = ["상의", "하의", "아우터"]
        default_cat_idx = 0
        if st.session_state.ai_analyzed:
            cat_val = st.session_state.analyzed_data["category"]
            if cat_val in categories:
                default_cat_idx = categories.index(cat_val)

        default_min = st.session_state.analyzed_data["temp_min"] if st.session_state.ai_analyzed else 10
        default_max = st.session_state.analyzed_data["temp_max"] if st.session_state.ai_analyzed else 25

        with st.form("add_form"):
            name = st.text_input("의류 이름", value=default_name)
            category = st.selectbox("카테고리", categories, index=default_cat_idx)
            temp_min = st.number_input("최저 기온 (℃)", value=default_min)
            temp_max = st.number_input("최고 기온 (℃)", value=default_max)

            submitted = st.form_submit_button("옷장에 추가하기")
            if submitted and name:
                img_to_save = st.session_state.img_bytes if st.session_state.ai_analyzed else None
                register_clothing(name, category, temp_min, temp_max, img_to_save)
                st.success(f"'{name}'이(가) 옷장에 추가되었습니다.")
                st.session_state.ai_analyzed = False
                st.session_state.analyzed_data = None
                st.session_state.img_bytes = None

    elif menu == "옷장 인벤토리 조회":
        st.subheader("등록된 의류 목록")

        conn = sqlite3.connect("modu_fit.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, category, temp_min, temp_max, image FROM wardrobe")
        items = cursor.fetchall()
        conn.close()

        if items:
            for item in items:
                col_a, col_b = st.columns([1, 3])
                with col_a:
                    if item[5]:
                        st.image(item[5], width=100)
                    else:
                        st.write("이미지 없음")
                with col_b:
                    st.write(f"**[{item[2]}] {item[1]}**")
                    st.write(f"적정 기온: {item[3]}℃ ~ {item[4]}℃")
                st.divider()
        else:
            st.write("등록된 의류가 없습니다.")