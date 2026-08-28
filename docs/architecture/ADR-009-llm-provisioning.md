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
