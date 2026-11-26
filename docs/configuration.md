# 소스 설정

개인 설정은 `sources.local.json`에 작성하고 MCP의 `--config` 인자로 지정합니다.
최상위 값은 JSON 배열입니다. 각 `id`는 중복 없이 영문·숫자·밑줄·하이픈 1~64자입니다.
`enabled`는 불리언이며 생략하면 활성입니다. `name`은 표시 이름입니다.

## 웹

```json
{
  "id": "my-news",
  "name": "웹 페이지",
  "kind": "web",
  "url": "https://example.com/news",
  "item_selector": "article",
  "title_selector": "h2",
  "link_selector": "h2 a",
  "author_selector": ".author",
  "detail_selector": ".article-body",
  "next_selector": "a.next",
  "max_pages": 2,
  "max_items": 20,
  "timeout_ms": 15000
}
```

`example.com`과 선택자는 설명용이며 실제 대상 DOM에 맞춰 수정해야 합니다.

| 필드 | 의미 |
|---|---|
| `url` | 최초 HTTP(S) URL. URL 내부 자격증명은 허용하지 않음 |
| `item_selector` | 목록의 항목. 필수 |
| `title_selector`, `link_selector` | 각 항목 내부의 제목과 링크. 필수 |
| `author_selector` | 항목 내부 작성자. 선택 |
| `body_selector` | 항목 내부 본문. 없으면 항목 전체 텍스트 |
| `detail_selector` | 개별 링크를 열어 읽을 본문. 지정하면 목록 본문 대신 사용 |
| `next_selector` | 다음 페이지 링크. 없으면 최초 페이지만 수집 |
| `max_pages` | 1~5. 기본 1 |
| `max_items` | 1~100. 기본 20 |
| `timeout_ms` | 페이지·선택자 대기 1~30,000 ms. 기본 15,000 |

선택자는 Playwright locator 문법입니다. HTML 대신 `inner_text()`로 텍스트를 가져옵니다.
상세·다음 페이지는 설정된 URL과 같은 origin만 허용합니다. 다음 페이지는 `href`가 있는
링크만 지원합니다. 무한 스크롤·버튼 펼치기·임의 JavaScript 실행은 지원하지 않습니다.
본문은 항목당 50만 자, 수집은 호출당 120초로 제한합니다.

같은 소스 URL은 재수집 시 중복 삽입하지 않고 변경 사항만 갱신합니다.
수집 범위 밖 글을 모두 복구하는 백필 기능은 없습니다. 선택자·상세 페이지 오류가 발생하면
해당 호출은 항목을 저장하지 않습니다.

## Slack 공개 채널 화면

```json
{
  "id": "slack",
  "name": "Slack 공개 채널",
  "kind": "slack_web",
  "enabled": false,
  "url": "https://app.slack.com/client/T123ABC/C123ABC",
  "max_messages": 500,
  "max_scrolls": 60,
  "max_threads": 5,
  "max_thread_scrolls": 5
}
```

`url`은 본인이 접근 가능한 공개 채널의 웹 주소로 바꿉니다. `max_messages`는
1~2000, `max_scrolls`는 1~100, `max_threads`는 0~20,
`max_thread_scrolls`는 1~20입니다. `max_threads: 0`이면 답글을 건너뜁니다.
`--login slack`으로 직접 로그인한 후 활성화합니다. 수집기는 채널을 위로
스크롤하며 로드된 각 메시지를 읽고, 답글이 있는 메시지의 스레드를 열어 답글도 읽습니다.
API 토큰을 사용하지 않으며, 전체 이력과 모든 답글의 완전 수집은 보장하지 않습니다.
설정 절차는 [README](../README.md#5-2-설정-파일에-고정한-채널-slack_web)를 참고하세요.

## 카카오톡 원문과 파일

`kakao_db`는 `room_title`, 선택적으로 절대 경로 `account_file`을 사용합니다.
`--config sources.local.json --setup-kakao kakao`를 실행하면 계정 정보를 검증하고
`account_file`을 자동으로 설정합니다. 방 이름과 `enabled`는 그대로 유지합니다.
`text_file`은 UTF-8 TXT의 절대 경로 `path`를 사용합니다.
설정과 권한 절차는 [README](../README.md#6-카카오톡-원문-수집과-권한)를 참고하세요.

## 레거시 접근성 소스

`kakao_window`는 `window_title`과 선택적 `capture_method: "accessibility"`를 받습니다.
macOS 손쉬운 사용과 System Events 자동화 권한으로 지정 창의 접근성 텍스트를 읽습니다.
이는 화면 스냅샷 기능이며 원문 이력 수집이 아닙니다. 앱이 접근성 창·텍스트를 노출하지
않으면 실패합니다. 새 카카오톡 원문 연결에는 `kakao_db`를 사용하세요. OCR은 지원하지 않습니다.
