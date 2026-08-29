# ansys-mcp

[![English](https://img.shields.io/badge/README-English-2563eb)](README.md)
[![한국어](https://img.shields.io/badge/README-%ED%95%9C%EA%B5%AD%EC%96%B4-16a34a)](README.ko.md)
[![日本語](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-dc2626)](README.ja.md)

ローカルにインストールされた Ansys システム上で、範囲を限定した熱解析ワークフローを実行する、閉じたインターフェースの MCP およびコマンドラインランナーです。公開リポジトリには、プロジェクトが所有するソース、スキーマ、生成されたテスト形状、およびテストのみが含まれます。Ansys または PyAnsys のチュートリアル、サンプルデータセット、製品ファイル、ドキュメント、ソルバー出力、qualification アーカイブは**再配布しません**。

本プロジェクトは独立したプロジェクトであり、Ansys の公式製品ではありません。

## サポート対象のコア

現在の実装は、次のローカル製品世代に対して検証されています。インストール検出はバージョンに動的に対応しますが、新しく検出されたリリースは、別途検証されるまでサポート対象とはみなしません。

| コンポーネント | 検証済みバージョン | 用途 |
| --- | --- | --- |
| Ansys Student | 2026 R1 (`261`) | ローカル製品のインストール |
| `ansys-common-mcp` | `0.3.3` | MCP 基盤 |
| `ansys-geometry-core` | `0.17.1` | 制限された CAD 検査 |
| `ansys-meshing-prime` | `0.10.4` | 熱解析メッシュの生成 |
| MAPDL | 2026 R1 | バッチ熱解析 |
| `ansys-dpf-core` | `0.16.1` | 結果の抽出 |
| `ansys-workbench-core` | `0.14.0` | オプションのライフサイクル機能 |
| `ansys-mechanical-core` | `0.13.2` | オプションの機能プローブ |
| Python | `3.12` | ランタイム |

Fluent、CFX、ACP、optiSLang、System Coupling、AEDT、EDB、LS-DYNA、Twin Runtime、Rocky、Speos、EnSight、TurboGrid、および Dynamic Reporting は、このリポジトリの公開実行インターフェースではありません。これらの製品に対する過去の互換性調査も、ここでは配布しません。

## ランナーの機能

- ドライブ、ユーザープロファイル、チェックアウト先を固定せず、標準の Ansys インストールを検出します。
- モデルおよび recipe の入力を、設定されたルートディレクトリ内に制限します。
- 明示的な単位を使用する厳密な Pydantic/YAML コントラクトを検証します。
- 閉じた semantic selector AST を使用して領域を解決します。
- 変更不能なソルバー中立 CAE-IR をコンパイルします。
- ローカルの SQLite WAL Registry にジョブを登録します。
- 固定された Prime → MAPDL → DPF 熱解析 worker を実行します。
- MCP 応答には field array を含めず、範囲を限定したサマリーと artifact hash のみを記録します。
- PID と生成時刻で正確に識別したプロセスツリーだけを所有し、終了処理を行います。

現在有効な v0.x の物理範囲は意図的に限定されています。等方性熱材料を持つ単一ソリッド、定常または非定常熱伝導、規定温度、対流、ならびに一様または範囲を限定した時系列の体積発熱をサポートします。サポートされない形状、selector、物理条件、およびライフサイクル状態は、安全側に拒否されます。

## MCP ツール

ローカル STDIO サーバーは、次の 10 個のツールを公開します。

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

チュートリアル catalog や実行機能はありません。いずれのツールも、Python、APDL、Scheme、journal、Workbench script、shell command、実行ファイルのパス、RPC endpoint、または呼び出し元が選択するソルバー switch を入力として受け付けません。

## インストール

```powershell
git clone https://github.com/miziyo/ansys-mcp.git
cd ansys-mcp
uv sync --frozen
uv run ansys-research doctor --json
```

標準インストールは自動的に検出されます。標準外のインストールは、現在のプロセスに限り次のように指定できます。

```powershell
$env:ANSYS_RESEARCH_ANSYS_ROOT = "<installation-root>"
```

マシン全体に適用される Ansys 設定は変更しません。

## MCP 設定

パッケージまたはツールをインストールし、`ansys-research-mcp` が `PATH` 上で利用可能になった後、次のように設定します。

```json
{
  "command": "ansys-research-mcp",
  "args": ["--transport", "stdio"]
}
```

ローカル STDIO のみを許可します。

## Pi 連携

Pi は MCP client を標準搭載していません。そのため、このリポジトリには、公式 MCP TypeScript SDK を介して同じ 10 個のツールを接続する、レビュー済みの Pi extension が含まれています。別の製品実行インターフェースを追加するものではありません。

`v0.13.0` リリースをインストールします。

```powershell
uv tool install "ansys-research-runner @ git+https://github.com/miziyo/ansys-mcp.git@v0.13.0" --python 3.12
pi install git:github.com/miziyo/ansys-mcp@v0.13.0
```

Pi を再起動するか `/reload` を実行してから、`/ansys-mcp-status` を使用してください。Extension は固定された `ansys-research-mcp --transport stdio` コマンドだけを起動し、サーバーが想定された 10 個のツールだけを公開していることを検証します。入力パスは現在の Pi プロジェクト内に制限され、変更可能な MCP 状態は Pi のユーザー設定ディレクトリに保存されます。

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

詳細は、[CLI ドキュメント](docs/cli.md)、[アーキテクチャ](docs/architecture.md)、および[サポート範囲](docs/supported-envelope.md)を参照してください。

## 公開コンテンツの境界

次の項目は、公開リポジトリおよびリリース artifact から意図的に除外されています。

- 公式または第三者のチュートリアルソースと notebook
- チュートリアル inventory、qualification matrix、および複製された説明文
- upstream のサンプルモデル、メディア、およびデータセット
- インストール済み製品の Help コンテンツまたはサンプルプロジェクト
- ソルバープロジェクト、mesh、結果、ログ、license データ、およびプロセス snapshot
- 生成された `runtime/`、`artifacts/`、`workspace/`、environment、および cache ディレクトリ

`src/ansys_research_runner/resources/geometry/` 配下の STEP ファイルは、寸法が文書化された、プロジェクト所有の隣接 Python ソースから生成されます。Ansys のサンプルを複製したものではありません。

リリース前に次の公開検証を実行してください。

```powershell
uv run python scripts/sanitize_tracked_paths.py
uv run python scripts/audit_public_repository.py --tree-only
```

## 開発

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ansys_research_runner
uv run python -m pytest tests/unit tests/property tests/contract tests/integration tests/fault_injection -q
uv build
```

Live test にはインストール済み製品と license が必要であり、デフォルトでは実行されません。

## ライセンスと商標

プロジェクト所有のソースは [MIT License](LICENSE) の下で提供されます。ランタイム依存関係は vendoring せず、それぞれのライセンスに従います。詳細は[第三者通知](THIRD_PARTY_NOTICES.md)を参照してください。

Ansys および Ansys 製品名は、Ansys, Inc. またはその関連会社の商標または登録商標です。ここでは、別途インストールされた互換製品を識別する目的でのみ使用しており、Ansys による保証または承認を意味しません。
