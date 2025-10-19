import requests

BASE_URL = "http://localhost:8000"
TOKEN = "y0_AgAAAAAbPr4ZAAG8XgAAAADNLuqwilBeWiUMSgy7sffy1X0Vc_Mt9t4"

def test_roast():
    print("\nТестируем генерацию прожарки:")
    response = requests.post(
        f"{BASE_URL}/roast",
        json={
            "provider": "yandex",
            "access_token": TOKEN,
            "playlist_kind": "liked",
            "generate_image": False,
        },
    )
    print(f"Статус: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print("\nИнформация о плейлисте:")
        print(f"- Название: {result['playlist']['title']}")
        print(f"- Количество треков: {result['playlist']['track_count']}")
        print("\nПрожарка:")
        print(result['roast'])
    else:
        print("Ошибка:", response.json())


test_roast()