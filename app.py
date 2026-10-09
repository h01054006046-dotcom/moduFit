import random
import requests
import sqlite3
import streamlit as st

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
            last_worn TEXT
        )
    """)
    conn.commit()
    conn.close()


def register_clothing(name, category, temp_min, temp_max):
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wardrobe (name, category, temp_min, temp_max, wear_count, last_worn)
        VALUES (?, ?, ?, ?, 0, '2026-09-30')
    """, (name, category, temp_min, temp_max))
    conn.commit()
    conn.close()


def get_current_weather():
    api_key = "ccbbadf63161257553c8cbbd8762e92c"
    city = "Seoul"
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"

    try:
        response = requests.get(url)
        data = response.json()
        return round(data["main"]["temp"])
    except:
        return random.randint(10, 25)


def recommend_outfit_from_db(current_temp):
    conn = sqlite3.connect("modu_fit.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, category, temp_min, temp_max, wear_count FROM wardrobe")
    rows = cursor.fetchall()
    conn.close()

    suitable_items = [
        {"name": row[0], "category": row[1], "temp_min": row[2], "temp_max": row[3], "wear_count": row[4]}
        for row in rows
        if row[2] <= current_temp <= row[3]
    ]

    if not suitable_items:
        return {"temp": current_temp, "outer": "아우터 불필요", "top": "조건에 맞는 상의 없음", "bottom": "조건에 맞는 하의 없음"}

    suitable_outers = [item["name"] for item in suitable_items if item["category"] == "아우터"]
    suitable_tops = [item["name"] for item in suitable_items if item["category"] == "상의"]
    suitable_bottoms = [item["name"] for item in suitable_items if item["category"] == "하의"]

    outer = random.choice(suitable_outers) if suitable_outers else "아우터 불필요"
    top = random.choice(suitable_tops) if suitable_tops else "상의 없음"
    bottom = random.choice(suitable_bottoms) if suitable_bottoms else "하의 없음"

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
        register_clothing("회색 전면프린팅 후드티", "상의", 10, 18)
        register_clothing("베이지 코튼팬츠", "하의", 15, 25)
        register_clothing("생지 데님", "하의", 15, 25)
        register_clothing("차콜 롱슬리브", "상의", 15, 25)
        register_clothing("체크 후드 자켓", "아우터", 10, 20)
        register_clothing("검은색 해링턴 자켓", "아우터", 10, 20)
        register_clothing("회색 전면프린팅 반팔티", "상의", 15, 25)
        register_clothing("검은색 전면프린팅 반팔티", "상의", 15, 25)
        register_clothing("검은색 코튼팬츠", "하의", 15, 25)
        register_clothing("회색 니트", "상의", 10, 18)
        register_clothing("자색 니트", "상의", 10, 18)

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
            current_temp = get_current_weather() if use_api else manual_temp
            result = recommend_outfit_from_db(current_temp)

            st.write(f"현재 기온: {result['temp']}℃")

            col1, col2, col3 = st.columns(3)
            col1.metric("추천 아우터", result["outer"])
            col2.metric("추천 상의", result["top"])
            col3.metric("추천 하의", result["bottom"])

    elif menu == "의류 등록 및 관리":
        st.subheader("새로운 의류 추가하기")

        # 스마트폰 카메라 연동 촬영 기능 추가
        st.markdown("#### 카메라 촬영 및 이미지 업로드")
        captured_image = st.camera_input("옷 사진이나 세탁 라벨을 촬영하세요")

        if captured_image is not None:
            st.image(captured_image, caption="촬영된 의류 이미지", width=300)
            st.info("사진이 정상적으로 업로드되었습니다. 아래 정보를 입력하여 등록해 주세요.")

        with st.form("add_form"):
            name = st.text_input("의류 이름")
            category = st.selectbox("카테고리", ["상의", "하의", "아우터"])
            temp_min = st.number_input("최저 기온 (℃)", value=10)
            temp_max = st.number_input("최고 기온 (℃)", value=25)

            submitted = st.form_submit_button("옷장에 추가하기")
            if submitted and name:
                register_clothing(name, category, temp_min, temp_max)
                st.success(f"'{name}'이(가) 옷장에 추가되었습니다.")

    elif menu == "옷장 인벤토리 조회":
        st.subheader("등록된 의류 목록")

        conn = sqlite3.connect("modu_fit.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, category, temp_min, temp_max FROM wardrobe")
        items = cursor.fetchall()
        conn.close()

        if items:
            for item in items:
                st.write(f"- [{item[2]}] {item[1]} (적정 기온: {item[3]}℃ ~ {item[4]}℃)")
        else:
            st.write("등록된 의류가 없습니다.")