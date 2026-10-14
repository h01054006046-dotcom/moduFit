"""weather.py - moduFit 날씨 기능 모듈

기온뿐 아니라 기상상황(비, 눈, 바람 등)을 가져와서
우산, 핫팩 같은 소지품을 추천한다.

- 소지품 추천은 규칙 기반이라 Gemini 호출이 필요 없다 (무료 한도 절약).
- 날씨 요약과 소지품 목록은 build_prompt_context()로 문자열로 만들어
  기존 Gemini 코디 추천 프롬프트에 붙이면 코디에도 날씨가 반영된다.
- OpenWeatherMap 무료 키로 쓸 수 있는 /weather, /forecast 엔드포인트만 사용한다.
"""
import os

import requests

BASE_URL = "https://api.openweathermap.org/data/2.5"
RAIN_TYPES = {"Rain", "Drizzle", "Thunderstorm"}


def get_weather(api_key, city="Seoul,KR", forecast_hours=12):
    """현재 날씨 + 앞으로 forecast_hours 시간 예보를 합쳐서 dict로 반환.

    실패하면 None을 반환하므로 호출하는 쪽에서 None 체크를 해야 한다.
    """
    try:
        params = {"q": city, "appid": api_key, "units": "metric", "lang": "kr"}
        cur = requests.get(f"{BASE_URL}/weather", params=params, timeout=5)
        cur.raise_for_status()
        cur = cur.json()

        # 예보는 3시간 간격이라 cnt = 시간 // 3
        fc_params = {**params, "cnt": max(1, forecast_hours // 3)}
        fc = requests.get(f"{BASE_URL}/forecast", params=fc_params, timeout=5)
        fc.raise_for_status()
        forecast = fc.json()["list"]

        # 현재 + 예보 구간을 한꺼번에 묶어서 계산
        temps = [cur["main"]["temp"]] + [f["main"]["temp"] for f in forecast]
        conditions = [cur["weather"][0]["main"]] + [
            f["weather"][0]["main"] for f in forecast
        ]
        rain_mm = [cur.get("rain", {}).get("1h", 0)] + [
            f.get("rain", {}).get("3h", 0) for f in forecast
        ]
        winds = [cur["wind"]["speed"]] + [f["wind"]["speed"] for f in forecast]

        return {
            "temp": cur["main"]["temp"],
            "feels_like": cur["main"]["feels_like"],
            "humidity": cur["main"]["humidity"],
            "condition": cur["weather"][0]["main"],      # 예: Rain, Snow, Clear
            "description": cur["weather"][0]["description"],  # 예: 가벼운 비
            "temp_min": min(temps),
            "temp_max": max(temps),
            "max_wind": max(winds),
            "max_pop": max((f.get("pop", 0) for f in forecast), default=0),  # 강수확률 0~1
            "rain": any(c in RAIN_TYPES for c in conditions),
            "snow": any(c == "Snow" for c in conditions),
            "heavy_rain": max(rain_mm) >= 5,
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


def recommend_items(w):
    """날씨 dict를 받아 [{'item': 소지품, 'reason': 이유}, ...] 반환."""
    items = []

    def add(item, reason):
        items.append({"item": item, "reason": reason})

    pop = round(w["max_pop"] * 100)

    # 비
    if w["rain"] or w["max_pop"] >= 0.5:
        add("우산", f"비 예보가 있어요 (강수확률 {pop}%)")
        if w["heavy_rain"]:
            add("방수 신발·여벌 양말", "비가 많이 올 수 있어요")
    elif w["max_pop"] >= 0.3:
        add("접이식 우산", f"비가 올 수도 있어요 (강수확률 {pop}%)")

    # 눈
    if w["snow"]:
        add("미끄럼 방지 신발", "눈이 와서 길이 미끄러울 수 있어요")
        add("우산 또는 모자", "눈을 맞을 수 있어요")

    # 추위 (체감온도 기준)
    if w["feels_like"] <= 5:
        add("핫팩", f"체감온도가 {w['feels_like']:.0f}°C로 추워요")
    if w["feels_like"] <= 0:
        add("장갑·목도리", "체감온도가 영하예요")

    # 더위
    if w["feels_like"] >= 30:
        add("물병·휴대용 선풍기", f"체감온도가 {w['feels_like']:.0f}°C로 더워요")
        add("모자·선크림", "햇볕이 강할 수 있어요")

    # 바람
    if w["max_wind"] >= 8:
        add("바람막이", f"바람이 강해요 (최대 {w['max_wind']:.0f}m/s)")

    # 일교차
    if w["temp_max"] - w["temp_min"] >= 10:
        add("가벼운 겉옷", "하루 중 기온 차이가 커요")

    return items


def build_prompt_context(w, items):
    """Gemini 프롬프트에 붙일 날씨 설명 문자열을 만든다."""
    item_text = ", ".join(i["item"] for i in items) if items else "없음"
    return (
        f"[오늘 날씨] 현재 {w['temp']:.0f}°C (체감 {w['feels_like']:.0f}°C), "
        f"{w['description']}, 습도 {w['humidity']}%, "
        f"예상 최저 {w['temp_min']:.0f}°C / 최고 {w['temp_max']:.0f}°C, "
        f"강수확률 {round(w['max_pop'] * 100)}%, 최대 풍속 {w['max_wind']:.0f}m/s\n"
        f"[챙길 소지품] {item_text}\n"
        "코디를 추천할 때 위 날씨를 반드시 고려하세요. "
        "비나 눈이 오면 젖기 쉬운 소재와 밝은 색 하의, 굽이 높거나 미끄러운 신발을 피하고, "
        "추우면 보온성을, 더우면 통풍이 잘되는 소재를 우선해 주세요."
    )


if __name__ == "__main__":
    # 단독 테스트: 터미널에서  OPENWEATHER_API_KEY=키 python weather.py
    weather = get_weather(os.environ.get("OPENWEATHER_API_KEY", ""))
    if weather is None:
        print("날씨를 가져오지 못했어요. API 키와 인터넷 연결을 확인하세요.")
    else:
        found = recommend_items(weather)
        print(build_prompt_context(weather, found))
        for it in found:
            print(f"- {it['item']}: {it['reason']}")
