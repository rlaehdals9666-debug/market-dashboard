# 내 마켓 대시보드 사용법

## 1. 처음 설치 (한 번만)

1. [python.org](https://www.python.org/downloads/)에서 파이썬을 설치하세요.
   Windows라면 설치 첫 화면에서 **"Add python.exe to PATH"** 에 꼭 체크하세요.
2. 이 폴더(`market-dashboard`)를 원하는 곳에 저장하세요.
3. 명령 프롬프트(Windows) 또는 터미널(Mac)을 열고 아래를 입력하세요.

```
cd 폴더경로/market-dashboard
pip install -r requirements.txt
```

## 2. 실행

```
streamlit run app.py
```

브라우저가 자동으로 열리면서 대시보드가 나타나요. 끌 때는 명령창에서 `Ctrl + C`.

## 3. 종목·키워드 바꾸기

`app.py`를 메모장으로 열고 위쪽 **설정** 부분만 수정하세요.

- 한국 주식: 종목코드 뒤에 코스피는 `.KS`, 코스닥은 `.KQ` (예: `"카카오": "035720.KS"`)
- 미국 주식: 티커 그대로 (예: `"마이크로소프트": "MSFT"`)
- 코인: 업비트 마켓 코드 (예: `"리플": "KRW-XRP"`)

## 4. 주간 일정 업데이트

인베스팅닷컴 경제 캘린더를 캡처해서 Claude에게 보내면 새 `schedule.json`을 만들어 드려요.
받은 파일을 이 폴더에 덮어쓰기 하면 끝이에요.

## 5. 휴대폰으로 보기 (선택)

1. [github.com](https://github.com)에 가입하고 이 폴더를 새 저장소에 올리세요.
2. [share.streamlit.io](https://share.streamlit.io)에서 GitHub로 로그인한 뒤 저장소와 `app.py`를 선택하면 링크가 생겨요.
3. 일정 업데이트는 GitHub에서 `schedule.json`만 교체하면 자동 반영돼요.

## 참고

- 코인 시세는 업비트 기준 실시간이에요.
- 주식 시세는 야후 파이낸스 기준이라 최대 15~20분 지연될 수 있어요. 실시간이 필요하면 다음 단계에서 한국투자증권 API로 교체할 수 있어요.
- 야후 파이낸스는 요청이 많으면 잠시 막힐 수 있어요. 그럴 땐 몇 분 뒤 자동으로 다시 불러와요.
