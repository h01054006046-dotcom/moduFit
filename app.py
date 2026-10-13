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
                   CREATE TABLE IF NOT EXISTS wardrobe
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       name
                       TEXT
                       NOT
                       NULL,
                       category
                       TEXT
                       NOT
                       NULL,
                       temp_min
                       INTEGER,
                       temp_max
                       INTEGER,
                       wear_count
                       INTEGER
                       DEFAULT
                       0,
                       last_worn
                       TEXT,
                       image
                       BLOB
                   )
                   """)
    conn.commit()
    conn.close()


def register_clothing(name, category, temp_min, temp_max, image_bytes=None):
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("""
                   INSERT INTO wardrobe (name, category, temp_min, temp_max, wear_count, last_worn, image)
                   VALUES (?, ?, ?, ?, 0, '2026-10-13', ?)
                   """, (name, category, temp_min, temp_max, image_bytes))
    conn.commit()
    conn.close()


def get_current_weather():
    try:
        api_key = st.secrets["WEATHER_API_KEY"]
        city = "Seoul"
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"

        response = requests.get(url, timeout=3)
        data = response.json()
        return round(data["main"]["temp"])
    except:
        return 18


def analyze_image_with_ai(image_bytes):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        client = genai.Client(api_key=api_key)

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

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt]
        )

        text_response = response.text.strip()
        if text_response.startswith("```"):
            lines = text_response.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            text_response = "\n".join(lines).strip()

        parsed_data = json.loads(text_response)
        cat = parsed_data.get("category", "상의")
        if cat not in ["상의", "하의", "아우터"]: cat = "상의"

        return {
            "name": parsed_data.get("name", "신규 의류"),
            "category": cat,
            "temp_min": int(parsed_data.get("temp_min", 10)),
            "temp_max": int(parsed_data.get("temp_max", 25))
        }
    except Exception as e:
        st.error(f"AI 분석 오류 발생: {e}")
        return {"name": "신규 의류", "category": "하의", "temp_min": 15, "temp_max": 25}


def ai_recommend_outfit(current_temp, wardrobe_items):
    """
    Gemini AI가 현재 기온과 옷장 목록을 분석하여 가장 어울리는 코디를 직접 선정하는 함수
    """
    if not wardrobe_items:
        return {"outer": None, "top": None, "bottom": None, "reason": "등록된 옷이 없습니다."}

    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        client = genai.Client(api_key=api_key)

        # 옷장 데이터를 JSON 문자열로 변환하여 AI에게 전달
        items_summary = [
            {"id": item[0], "name": item[1], "category": item[2], "temp_min": item[3], "temp_max": item[4]}
            for item in wardrobe_items
        ]

        prompt = f"""
        현재 서울의 기온은 {current_temp}℃ 입니다.
        아래 사용자의 옷장 인벤토리 목록 중에서 현재 기온에 가장 잘 어울리고 스타일 조합이 조화로운 '아우터', '상의', '하의'를 각각 정확히 하나씩 골라주세요.
        (단, 아우터는 날씨상 불필요하다면 null을 선택해도 됩니다. 상의와 하의는 반드시 옷장에 있는 항목의 id 중 하나를 골라야 합니다.)

        옷장 목록:
        {json.dumps(items_summary, ensure_ascii=False)}

        반드시 아래 JSON 형식으로만 응답하고 마크다운이나 다른 설명은 포함하지 마세요.
        {{
            "outer_id": 선택된 아우터의 id (없거나 불필요하면 null),
            "top_id": 선택된 상의의 id (필수),
            "bottom_id": 선택된 하의의 id (필수),
            "reason": "이 코디를 추천하는 AI의 센스 있는 이유 (한국어 한 문장)"
        }}
        """

        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=[prompt]
        )

        text_response = response.text.strip()
        if text_response.startswith("```"):
            lines = text_response.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            text_response = "\n".join(lines).strip()

        result_json = json.loads(text_response)

        # ID로 실제 옷 데이터 매핑
        item_dict = {item[0]: item for item in wardrobe_items}

        outer = item_dict.get(result_json.get("outer_id")) if result_json.get("outer_id") else None
        top = item_dict.get(result_json.get("top_id")) if result_json.get("top_id") else None
        bottom = item_dict.get(result_json.get("bottom_id")) if result_json.get("bottom_id") else None
        reason = result_json.get("reason", "오늘 날씨에 딱 어울리는 멋진 코디입니다!")

        return {
            "outer": {"id": outer[0], "name": outer[1], "category": outer[2], "temp_min": outer[3],
                      "temp_max": outer[4], "image": outer[5]} if outer else None,
            "top": {"id": top[0], "name": top[1], "category": top[2], "temp_min": top[3], "temp_max": top[4],
                    "image": top[5]} if top else None,
            "bottom": {"id": bottom[0], "name": bottom[1], "category": bottom[2], "temp_min": bottom[3],
                       "temp_max": bottom[4], "image": bottom[5]} if bottom else None,
            "reason": reason
        }
    except Exception as e:
        # AI 추천 실패 시 기온 조건에 맞는 옷 중 랜덤으로 대체
        suitable = [item for item in wardrobe_items if item[3] <= current_temp <= item[4]]
        if not suitable: suitable = wardrobe_items

        tops = [i for i in suitable if i[2] == "상의"]
        bottoms = [i for i in suitable if i[2] == "하의"]
        outers = [i for i in suitable if i[2] == "아우터"]

        t = random.choice(tops) if tops else (wardrobe_items[0] if wardrobe_items else None)
        b = random.choice(bottoms) if bottoms else (wardrobe_items[0] if wardrobe_items else None)
        o = random.choice(outers) if outers else None

        def fmt(x):
            return {"id": x[0], "name": x[1], "category": x[2], "temp_min": x[3], "temp_max": x[4],
                    "image": x[5]} if x else None

        return {
            "outer": fmt(o),
            "top": fmt(t),
            "bottom": fmt(b),
            "reason": f"AI 분석 중 오류가 발생하여 기온 맞춤형 대체 코디를 추천합니다. (오류: {e})"
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
        st.subheader("AI 센스 코디 추천")
        st.write("실시간 날씨와 옷장 목록을 Gemini AI가 분석하여 가장 어울리는 코디를 골라드립니다.")

        if st.button("AI 코디 추천받기"):
            current_temp = get_current_weather()

            conn = sqlite3.connect("modu_fit.db")
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, category, temp_min, temp_max, image FROM wardrobe")
            wardrobe_items = cursor.fetchall()
            conn.close()

            with st.spinner("Gemini AI가 오늘의 날씨와 옷장을 분석하여 코디를 조합하는 중입니다..."):
                result = ai_recommend_outfit(current_temp, wardrobe_items)

            st.info(f"현재 서울 기온: **{current_temp}℃**\n\n💡 **AI 스타일 코멘트**: {result['reason']}")

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