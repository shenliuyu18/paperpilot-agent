"""Export, verify and import PaperPilot research dossiers from the command line.

The archive is the same format used by ``GET /api/projects/{id}/dossier``:
a ZIP with ``manifest.json``, ``dossier.json`` and ``DOSSIER.md``. No PDFs or
credentials are included.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="使用隔离的数据目录（设置 PAPERPILOT_DATA_DIR）")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="导出项目档案")
    export.add_argument("--project-id", required=True)
    export.add_argument("--out", help="输出 .paperpilot.zip 路径")

    verify = sub.add_parser("verify", help="校验档案，不写入任何项目")
    verify.add_argument("file")
    verify.add_argument("--json", action="store_true", help="输出 JSON 摘要")

    import_cmd = sub.add_parser("import", help="校验后导入为新项目")
    import_cmd.add_argument("file")
    import_cmd.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.data_dir:
        os.environ["PAPERPILOT_DATA_DIR"] = args.data_dir
    from app.services import dossier  # imported after --data-dir is applied

    try:
        if args.command == "export":
            blob = dossier.export_project(args.project_id)
            out = Path(args.out) if args.out else Path(f"paperpilot-dossier-{args.project_id}.zip")
            out.write_bytes(blob)
            print(f"exported {out} ({len(blob)} bytes)")
            return 0
        source = Path(args.file)
        if not source.is_file():
            print(f"file not found: {source}", file=sys.stderr)
            return 2
        blob = source.read_bytes()
        if args.command == "verify":
            summary = dossier.verify_archive(blob)
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                project = summary["project"]
                counts = summary["summary"]
                print(f"OK {project['title']}")
                print(f"runs={counts['runs']} evidence={counts['evidence']} "
                      f"blocks={counts['draft_blocks']} version={summary['app_version']}")
                for warning in summary["warnings"]:
                    print(f"warning: {warning}")
            return 0
        result = dossier.import_archive(blob)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"imported {result['project']['id']} · {result['project']['title']}")
            print(f"runs={result['summary']['runs']} evidence={result['summary']['evidence']}")
        return 0
    except dossier.DossierError as exc:
        print(f"dossier error: {exc}", file=sys.stderr)
        return 2
    except KeyError:
        print("project not found", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
