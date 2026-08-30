<!-- standard-ai-workflow-kit: v1.6.0 -->

# ADR-009 — LLM 제공자를 한 곳에서 갈아 끼운다

- 상태: accepted
- 결정일: 2026-08-28
- 관련: [ADR-005](./ADR-005-llm-runtime.md), [ADR-001](./ADR-001-stack.md)

## 맥락

**LLM 설정 표면이 둘이다.**

| 표면 | 무엇이 쓰는가 | 어디에 설정하나 |
|---|---|---|
| 애플리케이션 게이트웨이 | 답변 생성 · 검수 · 임베딩 | `.env` (`ASD_LLM_*`) |
| **pi 하네스** | **지식 구축 에이전트** (D5) | `~/.pi/agent/models.json` |

둘은 별개 프로그램이므로 설정도 별개다. 그런데 **바꿀 때는 함께 바뀌어야 한다** —
개발에서는 원격 제공자, 운영에서는 로컬 GPU 서버로 가는데, 한쪽만 바뀌면 **지식은
로컬로 짓고 답변은 원격으로 하는** 뒤섞인 상태가 된다.

특히 위험한 조합이 하나 있다. **pi 만 원격을 가리키면 NFR-1 이 뚫린다** —
애플리케이션 게이트웨이는 정책(ADR-005 §결정 5)이 막지만, pi 는 우리 코드가 아니라
그 정책을 지나지 않는다. 지식 구축이야말로 **소스코드를 직접 읽는** 경로다.

## 결정

**`.env` 를 단일 출처로 삼고, pi 의 `models.json` 을 거기서 생성한다.**

| 설정 | 쓰는 곳 |
|---|---|
| `ASD_LLM_BASE_URL` · `ASD_LLM_MODEL` · `ASD_LLM_API_KEY` | 애플리케이션 게이트웨이 **+ pi** |
| `ASD_EMBEDDING_*` | 애플리케이션만 (pi 는 임베딩을 쓰지 않는다) |

pi 는 `apiKey` 에 `"$ENV_VAR"` 형태를 지원하므로 **키를 파일에 박지 않는다.**

```json
{
  "providers": {
    "asd": {
      "baseUrl": "<ASD_LLM_BASE_URL>",
      "api": "openai-completions",
      "apiKey": "$ASD_LLM_API_KEY",
      "models": [{ "id": "<ASD_LLM_MODEL>" }]
    }
  }
}
```

### NFR-1 검사를 생성 시점에 건다  {#gate}

생성기는 **애플리케이션과 같은 정책을 통과시킨다**(ADR-005 §결정 5). 실제 데이터에
닿는 실행에서 원격을 가리키면 **`models.json` 을 만들지 않고 거부한다.**

> pi 는 우리 정책을 지나지 않으므로, **설정을 만드는 시점이 유일한 검문소**다.

### 배포 시 바꾸는 것은 `.env` 하나  {#deploy}

```
운영:  ASD_LLM_BASE_URL=http://gpu-box.local:8000/v1
       ASD_LLM_MODEL=<로컬 모델>
       (ASD_LLM_ALLOW_REMOTE 불필요)

개발:  ASD_LLM_ALLOW_REMOTE=true
       ASD_PARENT_ADAPTER=mock
       ASD_LLM_BASE_URL=https://api.minimax.io/v1
       ASD_LLM_MODEL=MiniMax-M3
```

`.env` 를 바꾸고 생성기를 다시 돌리면 **애플리케이션과 pi 가 함께** 움직인다.

### pi 를 에이전트가 아니라 생성기로 부른다  {#non-agentic}

2026-08-30 실운영에서 밟았다. **pi 는 read·bash·edit 도구를 들고 `AGENTS.md`/
`CLAUDE.md` 를 찾아 읽는 코딩 에이전트**다. `-p` 하나만 주고 부르면 그 능력이 전부
켜진 채로 돌고, `cwd` 를 주지 않으면 **이 앱의 저장소**에서 돈다.

실제로 이런 응답이 왔다.

> "...the recent commits `a666f41`, `56cff5f`, `b469db8` (all visible in `git log`)
> plus the knowledge module code (`lint.py`, `config_values.py`, `policy.py`, ...)"

**전부 모 시스템이 아니라 우리 자신의 것이다.** 프롬프트로 준 원천 대신 작업
디렉터리를 뒤진 것이고, §5.3 이 QnA 쪽에서 막는 되먹임(W2)과 **같은 고장이 소스
쪽으로 난 것**이다. 게다가 도구를 쓰느라 턴을 소진해 빈 응답과 잘린 JSON 이 왔다.

> **이 경로에 필요한 것은 문장 하나를 받아 문장 하나를 내는 것뿐이다.**
> 그 밖의 능력은 전부 위험이므로 끌 수 있는 것을 다 끄고 **빈 임시 디렉터리**에서
> 부른다.

| 인자 | 무엇을 막는가 |
|---|---|
| `--no-tools` | 원천은 프롬프트로 준다. **뒤질 것이 있으면 뒤진다** |
| `--no-context-files` | `AGENTS.md`/`CLAUDE.md` 자동 로드 |
| `--no-extensions` · `--no-skills` · `--no-prompt-templates` | **재현성** — 로컬에 무엇이 깔려 있느냐로 지식이 달라지지 않게 |
| `--no-session` | 묶음 사이에 상태가 남는 것 |
| `cwd` = 빈 디렉터리 | 마지막 방어선. 기본값을 "호출자의 디렉터리"로 두면 그것이 곧 이 앱의 저장소다 |

**이것은 하네스를 차용한 대가다.** D5 가 pi 를 택한 것은 되돌릴 결정이 아니지만,
차용한 것이 *에이전트*라는 사실은 호출부가 매번 감당해야 한다 — pi 가 판올림되며
기본 능력이 늘면 그때마다 이 목록을 다시 봐야 한다.

## 대안과 기각 이유

| 대안 | 기각 이유 |
|---|---|
| 두 설정을 각각 관리 | 한쪽만 바뀌어 뒤섞인 상태가 된다. 특히 pi 만 원격이면 NFR-1 이 뚫린다 |
| pi 를 환경변수로만 설정 | pi 의 내장 제공자 이름(`minimax` 등)에 묶인다. 로컬 런타임으로 갈 때 방식이 달라진다 |
| `models.json` 을 손으로 관리 | 배포마다 두 곳을 고쳐야 하고, 잊으면 조용히 어긋난다 |
| pi 대신 직접 에이전트 구현 | D5 가 하네스 차용을 택했다. 되돌릴 결정이 아니다 |

## 귀결

- `models.json` 이 **생성물**이 된다 — 손으로 고치면 다음 생성에서 덮인다
- pi 의 제공자 이름을 `asd` 로 고정한다. 어느 제공자를 쓰든 pi 쪽 이름은 그대로다
- **키는 `.env` 에만 있고** `models.json` 에는 `$ASD_LLM_API_KEY` 참조만 남는다
