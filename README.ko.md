# ansys-mcp

[![English](https://img.shields.io/badge/README-English-2563eb)](README.md)
[![한국어](https://img.shields.io/badge/README-%ED%95%9C%EA%B5%AD%EC%96%B4-16a34a)](README.ko.md)
[![日本語](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-dc2626)](README.ja.md)

로컬에 설치된 Ansys 시스템에서 범위가 제한된 열 워크플로를 실행하는 폐쇄형 MCP 및 명령줄 러너입니다. 공개 저장소에는 프로젝트가 직접 소유한 소스, 스키마, 생성된 테스트 형상 및 테스트만 포함됩니다. Ansys 또는 PyAnsys 튜토리얼, 예제 데이터 세트, 제품 파일, 문서, 솔버 출력 및 qualification 아카이브는 **재배포하지 않습니다**.

이 프로젝트는 독립적으로 개발되었으며 Ansys의 공식 제품이 아닙니다.

## 지원 코어

현재 구현은 다음 로컬 제품 세대를 기준으로 검증되었습니다. 설치 탐색은 버전에 동적으로 대응하지만 새로 발견된 릴리스는 별도 검증을 통과하기 전까지 지원 대상으로 간주하지 않습니다.

| 구성 요소 | 검증 버전 | 용도 |
| --- | --- | --- |
| Ansys Student | 2026 R1 (`261`) | 로컬 제품 설치 |
| `ansys-common-mcp` | `0.3.3` | MCP 기반 기능 |
| `ansys-geometry-core` | `0.17.1` | 제한된 CAD 검사 |
| `ansys-meshing-prime` | `0.10.4` | 열 해석 메시 생성 |
| MAPDL | 2026 R1 | 배치 열 해석 |
| `ansys-dpf-core` | `0.16.1` | 결과 추출 |
| `ansys-workbench-core` | `0.14.0` | 선택적 수명주기 기능 |
| `ansys-mechanical-core` | `0.13.2` | 선택적 기능 검사 |
| Python | `3.12` | 런타임 |

Fluent, CFX, ACP, optiSLang, System Coupling, AEDT, EDB, LS-DYNA, Twin Runtime, Rocky, Speos, EnSight, TurboGrid 및 Dynamic Reporting은 이 저장소의 공개 실행 인터페이스로 제공되지 않습니다. 해당 제품에 대한 이전 호환성 조사 자료도 이곳에 배포하지 않습니다.

## 러너의 기능

- 드라이브, 사용자 프로필 또는 체크아웃 경로를 고정하지 않고 표준 Ansys 설치를 탐색합니다.
- 모델과 recipe 입력을 설정된 루트 경로 내부로 제한합니다.
- 명시적 단위를 사용하는 엄격한 Pydantic/YAML 계약을 검증합니다.
- 폐쇄형 semantic selector AST를 통해 영역을 해석합니다.
- 변경 불가능한 솔버 중립 CAE-IR을 컴파일합니다.
- 로컬 SQLite WAL Registry에 작업을 등록합니다.
- 고정된 Prime → MAPDL → DPF 열 해석 worker를 실행합니다.
- MCP 응답에서 field array를 제외하고 제한된 요약과 artifact hash만 기록합니다.
- PID와 생성 시각으로 정확히 식별한 프로세스 트리만 소유하고 정리합니다.

현재 활성화된 v0.x 물리 범위는 의도적으로 제한되어 있습니다. 등방성 열 재료를 갖는 단일 솔리드, 정상 또는 비정상 열전도, 규정 온도, 대류, 그리고 균일하거나 범위가 제한된 시계열 체적 발열을 지원합니다. 지원하지 않는 형상, selector, 물리 조건 및 수명주기 상태는 허용하지 않고 안전하게 실패합니다.

## MCP 도구

로컬 STDIO 서버는 다음 10개 도구를 제공합니다.

- `doctor`
- `inspect_model`
- `resolve_regions`
- `validate_run`
- `plan_run`
- `start_run`
- `get_run_status`
- `cancel_run`
- `get_run_summary`
- `list_run_artifacts`

튜토리얼 catalog나 실행기는 없습니다. 어떤 도구도 Python, APDL, Scheme, journal, Workbench script, shell command, 실행 파일 경로, RPC endpoint 또는 호출자가 지정한 솔버 switch를 입력으로 받지 않습니다.

## 설치

```powershell
git clone https://github.com/miziyo/ansys-mcp.git
cd ansys-mcp
uv sync --frozen
uv run ansys-research doctor --json
```

표준 설치는 자동으로 탐색합니다. 비표준 설치는 현재 프로세스에 한해 다음과 같이 지정할 수 있습니다.

```powershell
$env:ANSYS_RESEARCH_ANSYS_ROOT = "<installation-root>"
```

컴퓨터 전체에 적용되는 Ansys 설정은 변경하지 않습니다.

## MCP 설정

패키지 또는 도구를 설치해 `ansys-research-mcp`가 `PATH`에 등록된 후 다음과 같이 설정합니다.

```json
{
  "command": "ansys-research-mcp",
  "args": ["--transport", "stdio"]
}
```

로컬 STDIO만 허용합니다.

## Pi 연동

Pi는 기본 MCP client를 내장하지 않습니다. 따라서 이 저장소는 공식 MCP TypeScript SDK로 동일한 10개 도구를 연결하는 검토된 Pi extension을 포함합니다. 별도의 제품 실행 인터페이스를 추가하지 않습니다.

`v0.13.0` 릴리스를 설치합니다.

```powershell
uv tool install "ansys-research-runner @ git+https://github.com/miziyo/ansys-mcp.git@v0.13.0" --python 3.12
pi install git:github.com/miziyo/ansys-mcp@v0.13.0
```

Pi를 다시 시작하거나 `/reload`를 실행한 다음 `/ansys-mcp-status`를 사용하십시오. Extension은 고정된 `ansys-research-mcp --transport stdio` 명령만 실행하며, 서버가 정확히 10개의 예상 도구만 제공하는지 검증합니다. 입력 경로는 현재 Pi 프로젝트 내부로 제한하고 변경 가능한 MCP 상태는 Pi 사용자 설정 디렉터리에 저장합니다.

## CLI

```text
ansys-research doctor
ansys-research geometry-doctor
ansys-research solver-doctor --live
ansys-research inspect <model>
ansys-research resolve <recipe>
ansys-research validate <recipe>
ansys-research plan <recipe> [--run-id ID]
ansys-research run <recipe> [--run-id ID]
ansys-research status <run_id>
ansys-research cancel <run_id>
ansys-research results <run_id>
ansys-research artifacts <run_id>
ansys-research recover
```

자세한 내용은 [CLI 문서](docs/cli.md), [아키텍처](docs/architecture.md), [지원 범위](docs/supported-envelope.md)를 참조하십시오.

## 공개 콘텐츠 경계

다음 항목은 공개 저장소와 릴리스 artifact에서 의도적으로 제외합니다.

- 공식 또는 제3자 튜토리얼 소스와 notebook
- 튜토리얼 inventory, qualification matrix 및 복사된 설명문
- upstream 예제 모델, 미디어 및 데이터 세트
- 설치된 제품의 Help 콘텐츠 또는 예제 프로젝트
- 솔버 프로젝트, mesh, 결과, 로그, license 데이터 및 프로세스 snapshot
- 생성된 `runtime/`, `artifacts/`, `workspace/`, environment 및 cache 디렉터리

`src/ansys_research_runner/resources/geometry/` 아래의 STEP 파일은 치수가 문서화된, 프로젝트 소유의 인접 Python 소스로 생성됩니다. Ansys 예제를 복사한 파일이 아닙니다.

릴리스 전에 다음 공개 검증 절차를 실행하십시오.

```powershell
uv run python scripts/sanitize_tracked_paths.py
uv run python scripts/audit_public_repository.py --tree-only
```

## 개발

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ansys_research_runner
uv run python -m pytest tests/unit tests/property tests/contract tests/integration tests/fault_injection -q
uv build
```

Live test는 설치된 제품과 license가 필요하며 기본적으로 실행하지 않습니다.

## 라이선스 및 상표

프로젝트 소유 소스는 [MIT License](LICENSE)에 따라 배포됩니다. 런타임 의존성은 vendoring하지 않으며 각각의 라이선스를 그대로 따릅니다. 자세한 내용은 [제3자 고지](THIRD_PARTY_NOTICES.md)를 참조하십시오.

Ansys 및 Ansys 제품명은 Ansys, Inc. 또는 그 계열사의 상표 또는 등록상표입니다. 이곳에서는 별도로 설치된 호환 제품을 식별하는 목적으로만 사용하며 Ansys의 보증이나 승인을 의미하지 않습니다.
