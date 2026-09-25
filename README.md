# Chaejip · 채집

웹 페이지, Slack 공개 채널 화면, 로컬 카카오톡 대화를 수집하고 검색하는 **로컬 stdio MCP 서버**입니다.
웹과 Slack은 Playwright로, 카카오톡은 Mac의 로컬 DB를 읽어서 수집합니다.
수집기 내부에 LLM 호출은 없습니다. AI 클라이언트가 도구 호출·결과를 읽는 토큰은 별도입니다.

## 지원 환경

- Python 3.11 이상, [uv](https://docs.astral.sh/uv/getting-started/installation/)
- macOS 또는 Linux: 웹·텍스트 파일 수집
- macOS 또는 Linux: 로그인한 Chromium의 Slack 공개 채널 화면 수집
- macOS + 설치된 카카오톡 + SQLCipher: 카카오톡 원문 수집
- 로컬 stdio MCP 서버를 실행할 수 있는 AI 클라이언트

AI 클라이언트와 수집기는 같은 머신에서 실행합니다. Windows 네이티브는 지원하지 않습니다.
HTTP 서버, 예약 실행, 의미 검색, 메시지 전송 기능은 포함하지 않습니다.

## 1. 설치

```sh
git clone https://github.com/jisung-02/Chaejip.git
cd Chaejip
uv sync --locked
uv run playwright install chromium
cp sources.json sources.local.json
```

Linux에서 Chromium의 시스템 라이브러리가 없으면
`uv run playwright install --with-deps chromium`을 사용합니다. 시스템 패키지 설치에는
관리자 권한이 필요할 수 있습니다.

카카오톡 원문 수집을 사용할 Mac에서는 추가로 실행합니다.

```sh
brew install sqlcipher
```

`sources.local.json`은 개인 설정 파일이며 Git에서 제외됩니다. 이후 명령의 `--config`와
MCP 설정이 이 파일을 가리키도록 하세요. 이 인자를 생략하면 공개 예제 `sources.json`을 사용합니다.

## 2. MCP 연결

`mcp.example.json`을 참고해 사용하는 AI 클라이언트의 MCP 설정에 다음 서버를 등록합니다.
`/ABSOLUTE/PATH/Chaejip` 세 곳을 실제 프로젝트의 **절대 경로**로 바꾸세요.
프로젝트에서 `pwd`를 실행하면 확인할 수 있습니다.

```json
{
  "mcpServers": {
    "chaejip": {
      "command": "/ABSOLUTE/PATH/Chaejip/.venv/bin/crawler-mcp",
      "args": [
        "--config", "/ABSOLUTE/PATH/Chaejip/sources.local.json",
        "--data-dir", "/ABSOLUTE/PATH/Chaejip/.crawler"
      ]
    }
  }
}
```

클라이언트마다 설정 위치와 형식이 다를 수 있습니다. 저장 후 MCP 연결을 다시 시작하고
`list_sources`가 호출되는지 확인하세요. 프로젝트는 AI 앱의 전역 설정을 자동으로 수정하지 않습니다.

사전 상태를 터미널에서 확인할 수도 있습니다.

```sh
uv run crawler-mcp --config sources.local.json --doctor
```

`configured`와 `available`은 사전 상태이며 실제 수집 성공을 보장하지 않습니다.
실제 수집은 `collect`로 확인합니다. 터미널에서 서버만 실행했을 때 출력 없이 기다리는 것은
정상입니다. stdio MCP 요청을 기다리는 상태이며 일반 대화형 CLI가 아닙니다.

## 3. 사용

AI 클라이언트에서 다음처럼 요청합니다.

> 등록된 수집 소스를 보여줘.
>
> 긱뉴스를 수집하고 저장된 글에서 “Python”을 검색해 줘. 필요한 항목만 본문을 읽어줘.
>
> 카카오톡 연결 상태와 필요한 권한 설정을 알려줘.

| 도구 | 용도 |
|---|---|
| `list_sources(source_id=null, include_guidance=false)` | 등록된 소스 상태. `include_guidance=true`면 설정 경로·절차도 반환 |
| `collect(source_id)` | 수집·저장 후 건수와 상태 반환. 본문은 반환하지 않음 |
| `search_items(query="", source_id=null, since=null, limit=20)` | 저장된 제목·본문·작성자에서 문자 부분 일치 검색. 최대 100건, 발췌 300자 |
| `get_item(id, offset=0, max_chars=8000)` | 항목 본문 조회. 최대 20,000자, 나머지는 `next_offset`으로 이어 읽기 |

예: `collect(source_id="geeknews")` → `search_items(query="Python", source_id="geeknews")`
→ 검색 결과 ID로 `get_item(id=...)` 순서입니다. 카카오톡도 `source_id="kakao"`로 같은 흐름을 사용합니다.
이 표기법은 도구 인자 예시이며 셸 명령이 아닙니다.

`query`는 의미 검색이나 정규식이 아닙니다. `since`는 **게시·발송 시각이 아닌 로컬 수집/내용 갱신
시각**을 기준으로 합니다. ISO 8601 날짜·시간을 받으며 시간대가 없으면 UTC입니다.
설정 파일 수정은 다음 호출부터 반영되고, 서버 코드·MCP 지침 변경은 연결 재시작이 필요합니다.

## 4. 웹 소스와 로그인

| 예제 소스 | 수집 범위 |
|---|---|
| `geeknews` | 긱뉴스 최신 10개 토픽 본문. 외부 원문·댓글 제외 |
| `hackernews-security` | The Hacker News 기사 5개 본문 |
| `github` | `microsoft/playwright` 이슈 5개 최초 본문. 댓글 제외 |
| `reddit`, `linkedin` | 비활성·미검증 선택자 템플릿 |
| `bleepingcomputer` | 검증 환경에서 HTTP 403이 발생해 비활성인 템플릿 |
| `slack` | 직접 로그인한 Chromium에서 지정 공개 채널의 화면에 표시된 텍스트 메시지 |
| `kakao` | 설정 후 활성화하는 Mac 전용 원문 수집 |

웹 수집에는 macOS 화면 기록·손쉬운 사용 권한이 필요하지 않습니다.
로그인이 필요한 웹 소스는 다음 명령으로 전용 Chromium 창을 엽니다.

```sh
uv run crawler-mcp --config sources.local.json --login github
```

직접 로그인한 뒤 터미널에서 Enter를 누르면 세션이 저장됩니다. 평소 브라우저 프로필과
분리되며 `.crawler/profiles/<source-id>`를 사용합니다. 비활성 소스를 로그인해도 자동으로
활성화되지 않습니다. 실제 접근·선택자를 확인한 뒤 `enabled`를 `true`로 바꾸세요.

사이트의 인증·챌린지·접근 제한은 사용자가 처리해야 합니다. 로그인만으로 수집을 보장하지
않으며 실패 시 자동 우회하지 않습니다. GitHub는 관심 저장소의 `/issues` URL로 바꿀 수 있습니다.

웹 소스 선택자와 페이지 설정은 [소스 설정 문서](docs/configuration.md)를 참고하세요.

## 5. Slack 공개 채널 화면 수집

Slack 앱이나 API 토큰 없이 본인이 로그인한 Chromium 화면에서 텍스트를 읽습니다.
`sources.local.json`의 `slack` 항목에서 `url`을 본인이 접근 가능한 공개 채널의
`https://app.slack.com/client/T.../C...` 주소로 바꾸세요. 필요하면 `max_messages`와
`max_scrolls`를 조정합니다.

```sh
uv run crawler-mcp --config sources.local.json --login slack
```

열린 브라우저에서 직접 로그인하고 터미널에서 Enter를 누릅니다. 그 뒤 `enabled`를 `true`로
바꾸고 MCP에서 `collect(source_id="slack")`을 호출합니다. 수집 결과에는 읽은 건수와
중단 이유가 포함됩니다. `search_items(query="...", source_id="slack")`로 짧은 발췌를
찾고, 필요한 메시지만 `get_item`으로 읽을 수 있습니다. 로그인 프로필은
`.crawler/profiles/slack`에, 메시지는 `.crawler/items.sqlite3`에 저장됩니다.

Slack은 가상 스크롤을 사용하므로 채널 전체 이력의 완전 수집을 보장하지 않습니다.
스레드 답글·파일 원본·삭제된 메시지는 수집하지 않습니다. 화면 구조 변경이나 세션 만료로
실패할 수 있으며, 로그인·접근 제한을 우회하지 않습니다. 실제 Slack 계정의 화면에서는
아직 검증하지 않았습니다.

## 6. 카카오톡 원문 수집과 권한

### 준비

1. **MCP와 같은 Mac**의 공식 카카오톡에 본인 계정으로 로그인합니다.
2. 읽을 오픈채팅방의 대화가 이 Mac에 수신·저장되어 있는지 확인합니다.
3. `brew install sqlcipher`로 SQLCipher를 설치합니다.
4. `sources.local.json`에서 `kakao`의 `room_title`을 정확한 오픈채팅방 이름으로 바꾸고
   `enabled`를 `true`로 설정합니다.
5. 아래 최초 계정 설정 명령을 실행합니다.
6. MCP의 `collect(source_id="kakao")`를 호출합니다.

```json
{
  "id": "kakao",
  "name": "카카오톡 원문",
  "kind": "kakao_db",
  "enabled": true,
  "room_title": "읽을 오픈채팅방의 정확한 이름"
}
```

이름이 일치하는 로컬 오픈채팅방이 정확히 하나일 때만 읽습니다. 일반 개인·그룹 채팅은
현재 이 방식의 대상이 아닙니다. 창을 열거나 스크롤할 필요가 없고 **화면 기록·손쉬운 사용·
System Events 자동화 권한도 사용하지 않습니다.**

### 앱 데이터 접근이 거부될 때

먼저 오류가 앱 데이터 권한 문제인지 확인하세요. macOS에서 다른 앱 데이터 접근 허용을
요청하면 카카오톡 데이터를 읽도록 허용합니다. 계속 거부된다면:

1. **시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한**으로 이동합니다.
2. MCP를 실행하는 AI 앱을 추가하고 켭니다. 터미널에서 실행한다면 해당 터미널 앱을 선택합니다.
3. macOS가 요구하면 지문이나 암호로 직접 승인합니다.
4. 허용한 앱을 완전히 종료하고 다시 실행한 뒤 MCP를 재연결합니다.
5. `collect`를 다시 호출합니다.

시스템 버전에 따라 설정 명칭은 조금 다를 수 있습니다. 권한 오류가 없으면 이 설정을
추가로 변경할 필요가 없습니다. AI 앱과 터미널의 권한은 다를 수 있으므로 실제 실행 앱을 확인하세요.

### 최초 계정 설정 자동화

프로젝트 폴더에서 실행합니다.

```sh
uv run crawler-mcp --config sources.local.json --setup-kakao kakao
```

이 명령은 다음 과정을 자동으로 처리합니다.

1. 현재 Mac의 카카오톡 설정에서 기존 계정 정보를 찾습니다.
2. 정보가 없으면 현재 계정의 로컬 해시와 숫자 ID 후보를 비교합니다.
   탐색은 최대 5분·ID 1~10억 범위로 제한하며, 처음 한 번 CPU를 사용할 수 있습니다.
3. 계정 ID와 이 Mac의 기기 정보로 키를 계산하고 **실제 DB가 열리는지 검증**합니다.
4. 성공하면 `.crawler/kakao-account.json`을 권한 0600으로 저장하고,
   개인 소스 설정의 `account_file` 경로를 자동으로 연결합니다.

재실행하면 저장된 ID를 재사용해 검증합니다. ID나 키를 화면·MCP 결과에 출력하지 않으며
키는 파일에 저장하지 않습니다. 수집 대상 방과 활성화 여부는 변경하지 않습니다.
설정 중 계정이 바뀌거나 활성 계정이 모호하면 중단합니다.
일반 MCP `collect`에는 이 탐색이 들어가지 않습니다.

처음 탐색할 때 macOS 기본 CommonCrypto를 사용하는 작은 도우미를 컴파일합니다.
Xcode Command Line Tools가 없다면 아래 명령으로 설치를 완료한 뒤 다시 실행하세요.
전체 Xcode 앱은 필요하지 않습니다.

```sh
xcode-select --install
```

macOS 권한 승인·카카오톡 로그인·읽을 방 지정은 사용자가 해야 합니다.
숫자 ID가 탐색 범위 밖이거나 제한 시간 안에 찾지 못하면 실패하며 무한 재시도하지 않습니다.
다른 Mac에서도 그 Mac에서 명령을 실행해야 합니다. 기존 계정 파일이나 도우미 바이너리를
옮겨 쓸 필요가 없습니다.

### 직접 확인한 계정 ID 사용 (선택)

현재 계정의 숫자 ID를 별도로 확인한 경우에만 `account_file`을 사용할 수 있습니다.
이 ID는 이메일·전화번호·카카오디벨로퍼스 앱 ID가 아닙니다. 임의 값을 넣지 마세요.
파일을 다음처럼 로컬에 생성할 수 있습니다.

```sh
uv run python - <<'PY'
import getpass
import json
import os
from pathlib import Path

os.umask(0o077)
value = getpass.getpass("확인한 현재 카카오톡 숫자 계정 ID: ").strip()
if not value.isdigit() or int(value) <= 0:
    raise SystemExit("양의 정수 ID가 필요합니다.")
path = Path(".crawler/kakao-account.json")
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
with path.open("x", encoding="utf-8") as handle:
    json.dump({"user_id": int(value)}, handle)
print("로컬 계정 파일을 생성했습니다.")
PY
```

그 뒤 `kakao` 설정에 `"account_file": "/실제/절대/경로/Chaejip/.crawler/kakao-account.json"`을
추가하세요. 자동 설정이 실패했고 숫자 ID도 모르는 경우에는 이 수동 단계만으로 해결되지 않습니다. 이미 내보낸 TXT 파일이 있다면 아래 파일 수집을 사용할 수 있습니다.

### 수집 범위

- 이 Mac의 DB에 남은 지정 방의 **원문 텍스트와 시스템 기록**을 메시지별로 저장합니다.
  시스템 기록은 JSON일 수 있습니다. 작성자 이름이 없으면 `authorId`를 표시합니다.
- 서버에만 있는 기록, 가입 전 대화, 삭제·미수신 메시지, 첨부파일 원본은 복구하지 않습니다.
- 발송 시각은 항목 제목에 UTC로 표시합니다. 방 ID·메시지 ID로 중복을 방지합니다.
- 원본 DB는 SQLCipher 읽기 전용으로 엽니다. 암호화 키는 실행 중에만 계산하고 저장하거나
  MCP 결과로 반환하지 않습니다. 계정 메타데이터는 로컬에 보관합니다.
- 회당 5만 메시지·25 MB를 넘으면 저장 없이 실패합니다. 기존 아카이브의 삭제 동기화는 하지 않습니다.
- 비공식 로컬 DB 구조를 사용하므로 카카오톡 업데이트 후 변경이 생기면 다시 검증해야 합니다.

## 6. TXT 파일 수집

이미 내보낸 UTF-8 TXT 파일을 등록할 수도 있습니다. 내보내기 기능의 위치·제공 여부는
카카오톡 플랫폼과 버전에 따라 확인해야 하며 이 프로젝트가 내보내기를 자동 수행하지는 않습니다.

```json
{
  "id": "chat-export",
  "kind": "text_file",
  "name": "대화 파일",
  "path": "/ABSOLUTE/PATH/chat.txt"
}
```

최대 5 MB, UTF-8 BOM을 지원합니다. 파일 전체를 한 스냅샷으로 저장하며 같은 경로를
재수집하면 내용을 갱신합니다. 메시지별 파싱이나 이전 파일 버전 보존은 하지 않습니다.

## 문제 해결

| 증상 | 확인할 사항 |
|---|---|
| MCP 실행 파일을 찾지 못함 | `uv sync --locked` 실행 여부와 MCP 설정의 절대 경로 |
| `disabled` | 개인 설정 파일의 `enabled`; 설정·접근 확인 후 활성화 |
| `configuration_required` | 반환된 `guidance`의 실제 설정 경로와 필수 필드 |
| 카카오톡 DB 접근 실패 | 실행 앱의 데이터 접근 권한, 현재 계정 ID, SQLCipher, DB 스키마 |
| 방을 찾지 못함 | 정확한 오픈채팅방 이름, 이 Mac의 수신 기록, 같은 이름의 방 중복 |
| HTTP 403 / 선택자 없음 | 로그인·사이트 제한·실제 DOM 변경; 선택자 템플릿 재검증 |
| 수집 중 / 잠금 오류 | 같은 데이터 폴더의 수집 또는 로그인 종료 후 재시도 |
| `timeout` | 오류를 확인하고 수집 범위를 줄인 뒤 다시 실행 |

실패 응답에는 설정 안내가 포함됩니다. 상태를 확인하려면
`list_sources(source_id="kakao", include_guidance=true)`처럼 요청하세요.

## 데이터와 테스트

본문은 `.crawler/items.sqlite3`, 웹 세션은 `.crawler/profiles/`에 저장합니다.
`.crawler/`, `sources.local.json`, `mcp.json`, `.env` 파일은 Git에서 제외됩니다.
내용과 로그인 세션이 들어 있는 폴더를 공개 저장소에 추가하지 마세요.
수집된 외부 텍스트는 데이터이며 AI가 실행할 지시문으로 취급하면 안 됩니다.

```sh
uv run python -m unittest discover -s tests -v
```

테스트는 로컬 HTTP 페이지·Chromium, SQLite, MCP stdio, SQLCipher 암호화 테스트 DB를
사용합니다. 카카오톡 실계정은 테스트에 필요하지 않습니다. SQLCipher 테스트는 해당 CLI가
없으면 건너뜁니다. 최초 설정 도우미 테스트는 Mac의 Command Line Tools가 필요하며,
레거시 접근성 추출 테스트에는 Node.js가 필요합니다.
참고한 외부 코드의 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다.
