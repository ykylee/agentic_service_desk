"""pi 헤드리스 실행 (D5, ADR-009).

**지식 구축이 이 경로로 돈다.** 여기서 정리하지 않으면 모델의 사고 과정이 그대로
지식 항목이 된다 — pi 는 `reasoning_split` 을 보내지 않고, `--thinking off` 나
모델 설정의 `reasoning: false` 로도 꺼지지 않는다(2026-08-28 확인).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_service_desk.ingest.harness_runner import HarnessError, HarnessResult, PiHarness


class _Proc:
    def __init__(self, out: str = "", err: str = "", code: int = 0) -> None:
        self.stdout, self.stderr, self.returncode = out, err, code


class TestOutputCleaning:
    def test_사고_블록을_걷어낸다(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _Proc("<think>\n생각\n</think>\n\n답변")
        )
        r = PiHarness("M", "key").run("무엇이든")
        assert r.text == "답변"
        assert r.had_thinking is True

    def test_원본을_남긴다(self, monkeypatch) -> None:
        # 무엇이 걷혔는지 추적해야 할 때가 있다.
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc("<think>가</think>나"))
        assert "<think>" in PiHarness("M", "key").run("x").raw

    def test_사고가_없으면_그대로다(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc("답변만"))
        r = PiHarness("M", "key").run("x")
        assert r.text == "답변만"
        assert r.had_thinking is False


class TestCalledAsGeneratorNotAgent:
    """pi 를 **생성기로** 부르는가 (2026-08-30 실운영에서 밟았다).

    pi 는 read·bash·edit 도구를 들고 `AGENTS.md`/`CLAUDE.md` 를 찾아 읽는 코딩
    에이전트다. 그대로 부르면 **프롬프트로 준 원천 대신 작업 디렉터리를 뒤진다** —
    실제로 이 앱 자신의 커밋과 파일을 읽고 그것을 지식으로 내놓았고, 도구를 쓰느라
    턴을 소진해 빈 응답과 잘린 JSON 이 왔다.

    지식 구축 에이전트가 **우리를 읽는 것**은 §5.3 이 QnA 쪽에서 막는 되먹임과
    같은 고장이 소스 쪽으로 난 것이다.
    """

    def _cmd_and_cwd(self, monkeypatch, **run_kw):  # noqa: ANN001, ANN202
        seen: dict = {}

        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            seen["cmd"] = cmd
            seen["cwd"] = kw.get("cwd")
            return _Proc("ok")

        monkeypatch.setattr(subprocess, "run", fake)
        PiHarness("M", "key").run("프롬프트", **run_kw)
        return seen

    @pytest.mark.parametrize(
        "flag",
        [
            "--no-tools",
            "--no-context-files",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-session",
        ],
    )
    def test_능력을_끈다(self, monkeypatch, flag: str) -> None:
        assert flag in self._cmd_and_cwd(monkeypatch)["cmd"]

    def test_빈_디렉터리에서_돈다(self, monkeypatch) -> None:
        # **기본값이 호출자의 디렉터리면 그것이 이 앱의 저장소다.**
        seen = self._cmd_and_cwd(monkeypatch)
        cwd = Path(seen["cwd"])
        assert cwd != Path.cwd()
        assert "asd-ingest-" in cwd.name

    def test_준_디렉터리가_있으면_그것을_쓴다(self, monkeypatch, tmp_path) -> None:
        seen = self._cmd_and_cwd(monkeypatch, cwd=str(tmp_path))
        assert seen["cwd"] == str(tmp_path)


class TestKeyHandling:
    def test_키를_명령줄에_넣지_않는다(self, monkeypatch) -> None:
        # 명령줄 인자는 프로세스 목록에 노출된다.
        seen: dict = {}

        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            seen["cmd"] = cmd
            seen["env"] = kw.get("env", {})
            return _Proc("ok")

        monkeypatch.setattr(subprocess, "run", fake)
        PiHarness("M", "비밀키").run("프롬프트")
        assert "비밀키" not in " ".join(seen["cmd"])
        assert seen["env"]["ASD_LLM_API_KEY"] == "비밀키"

    def test_환경변수_이름이_models_json_참조와_맞는다(self, monkeypatch) -> None:
        # ADR-009 — models.json 이 "$ASD_LLM_API_KEY" 를 참조한다. 이름이 어긋나면
        # pi 가 키를 못 찾는데, 그 실패는 런타임에야 드러난다.
        from agentic_service_desk.llm.harness import render_models_json
        from agentic_service_desk.config import Settings

        cfg = Settings(_env_file=None, llm_base_url="http://x.local/v1", llm_model="M",
                       parent_adapter="http")  # type: ignore[arg-type]
        ref = render_models_json(cfg)["providers"]["asd"]["apiKey"]

        seen: dict = {}
        monkeypatch.setattr(subprocess, "run",
                            lambda cmd, **kw: (seen.update(env=kw.get("env", {})), _Proc("ok"))[1])
        PiHarness("M", "k").run("x")
        assert ref == "$" + next(k for k in seen["env"] if k == "ASD_LLM_API_KEY")


class TestFailures:
    def test_pi_가_없으면_알려준다(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
        )
        with pytest.raises(HarnessError, match="찾을 수 없다"):
            PiHarness("M", "k", executable="없는명령").run("x")

    def test_시간_초과를_알려준다(self, monkeypatch) -> None:
        def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise subprocess.TimeoutExpired(cmd="pi", timeout=1)

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(HarnessError, match="끝나지 않았다"):
            PiHarness("M", "k", timeout=1).run("x")

    def test_실패_코드에_stderr_를_담아_올린다(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc("", "인증 실패", 1))
        with pytest.raises(HarnessError, match="인증 실패"):
            PiHarness("M", "k").run("x")


class TestResult:
    def test_공백만_다른_경우는_사고로_치지_않는다(self) -> None:
        assert HarnessResult(text="답", raw="  답  ").had_thinking is False
