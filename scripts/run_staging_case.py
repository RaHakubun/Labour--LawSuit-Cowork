#!/usr/bin/env python3
"""Run the de-identified unlawful-termination case against a real staging API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx


TERMINAL_EVENTS = {
    "operation.completed",
    "operation.failed",
    "operation.cancelled",
}


class StagingCaseRunner:
    def __init__(self, *, api_base: str, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30, read=None),
        )
        self._sequence = 0
        self._reconnect_verified = False

    async def close(self) -> None:
        await self._client.aclose()

    async def run(
        self,
        *,
        contract: Path,
        wage_record: Path,
        conflicting_wage_record: Path,
        scanned_notice: Path,
    ) -> dict[str, Any]:
        case = await self._request("POST", "/cases", json={"role_id": "worker"})
        case_id = case["case_id"]
        await self._command(
            case_id,
            {
                "command_type": "submit_user_message",
                "text": (
                    "我在示例科技公司工作，公司以绩效不合格为由口头通知解除劳动合同，"
                    "没有说明考核依据。我希望核对违法解除赔偿、未付加班费，并准备劳动仲裁。"
                ),
            },
        )
        await self._command(
            case_id,
            {
                "command_type": "submit_user_message",
                "text": (
                    "入职、工资、解除时间以我随后上传的去身份化材料为准；工作地为上海，"
                    "合同签订地为杭州，我没有签收书面解除通知。"
                ),
            },
        )
        for path in (contract, wage_record, scanned_notice, conflicting_wage_record):
            await self._upload(case_id, path)

        current = await self._case(case_id)
        for conflict in current["fact_conflicts"]:
            await self._command(
                case_id,
                {
                    "command_type": "confirm_fact",
                    "fact_id": conflict["fact_id"],
                    "value": conflict["incoming"]["value"],
                    "conflict_id": conflict["conflict_id"],
                },
            )
        current = await self._case(case_id)
        for fact in current["candidate_facts"]:
            if fact["status"] == "pending_verification":
                await self._command(
                    case_id,
                    {
                        "command_type": "confirm_fact",
                        "fact_id": fact["fact_id"],
                        "value": fact["value"],
                    },
                )

        await self._command(
            case_id,
            {
                "command_type": "submit_user_message",
                "text": "材料中的候选事实已经确认，请继续检索适用法源并完成赔偿规则计算。",
            },
        )
        current = await self._case(case_id)
        wage = next(
            item
            for item in current["candidate_facts"]
            if item["fact_id"] == "employment.monthly_wage"
            and item["status"] == "confirmed"
        )
        await self._command(
            case_id,
            {
                "command_type": "calculate_rule",
                "calc_type": "wage_base",
                "inputs": {"monthly_wage": wage["value"]},
                "fact_ids": [wage["fact_id"]],
            },
        )
        await self._command(case_id, {"command_type": "request_analysis"})
        await self._command(
            case_id,
            {
                "command_type": "request_document",
                "document_type": "labour_arbitration_application",
            },
        )
        await self._command(
            case_id,
            {
                "command_type": "submit_user_message",
                "text": "补充：解除当日公司收回了门禁权限，请更新分析及文书版本。",
            },
        )
        await self._command(case_id, {"command_type": "request_analysis"})
        await self._command(
            case_id,
            {
                "command_type": "request_document",
                "document_type": "labour_arbitration_application",
            },
        )

        final = await self._case(case_id)
        artifact_types = {item["artifact_type"] for item in final["artifacts"]}
        required_artifacts = {
            "legal_analysis_report",
            "labour_arbitration_application",
        }
        failures = []
        if not final["authorities"]:
            failures.append("no authority was committed")
        if not final["rule_results"]:
            failures.append("no rule result was committed")
        if not required_artifacts.issubset(artifact_types):
            failures.append("required analysis/document artifacts are missing")
        if not any(item["revision_count"] >= 2 for item in final["artifacts"]):
            failures.append("artifact revision update was not observed")
        if not self._reconnect_verified:
            failures.append("SSE reconnect was not exercised")
        if failures:
            raise RuntimeError("staging acceptance failed: " + "; ".join(failures))
        return {
            "case_id": case_id,
            "case_version": final["version"],
            "event_sequence": self._sequence,
            "authority_count": len(final["authorities"]),
            "rule_result_count": len(final["rule_results"]),
            "artifacts": final["artifacts"],
            "sse_reconnect_verified": self._reconnect_verified,
        }

    async def _case(self, case_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/cases/{case_id}")

    async def _command(self, case_id: str, payload: dict[str, Any]) -> None:
        current = await self._case(case_id)
        accepted = await self._request(
            "POST",
            f"/cases/{case_id}/commands",
            json={
                "idempotency_key": f"staging-{payload['command_type']}-{os.urandom(12).hex()}",
                "expected_case_version": current["version"],
                "payload": payload,
            },
        )
        await self._wait_terminal(case_id, accepted["command_id"])

    async def _upload(self, case_id: str, path: Path) -> None:
        current = await self._case(case_id)
        media_type = {
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".json": "application/json",
            ".pdf": "application/pdf",
            ".docx": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(path.suffix.lower())
        if media_type is None:
            raise ValueError(f"unsupported staging evidence extension: {path.suffix}")
        with path.open("rb") as handle:
            accepted = await self._request(
                "POST",
                f"/cases/{case_id}/evidence",
                params={
                    "idempotency_key": f"staging-evidence-{os.urandom(12).hex()}",
                    "expected_case_version": current["version"],
                },
                files={"file": (path.name, handle, media_type)},
            )
        await self._wait_terminal(case_id, accepted["command_id"])

    async def _wait_terminal(self, case_id: str, command_id: str) -> None:
        reconnect_once = not self._reconnect_verified
        while True:
            headers = {"Last-Event-ID": str(self._sequence)}
            async with self._client.stream(
                "GET",
                f"/cases/{case_id}/events",
                params={"after_sequence": self._sequence},
                headers=headers,
            ) as response:
                await self._raise_for_status(response)
                event_name = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_name = line[7:]
                    elif line.startswith("data: "):
                        event = json.loads(line[6:])
                        self._sequence = max(self._sequence, int(event["sequence"]))
                        if reconnect_once:
                            reconnect_once = False
                            self._reconnect_verified = True
                            break
                        if event["command_id"] != command_id:
                            continue
                        if event_name in TERMINAL_EVENTS:
                            if event_name != "operation.completed":
                                raise RuntimeError(
                                    f"{event_name}: {json.dumps(event['payload'], ensure_ascii=False)}"
                                )
                            return

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        await self._raise_for_status(response)
        return response.json()

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = await response.aread()
            message = detail.decode("utf-8", errors="replace")
        except Exception:
            message = "response body unavailable"
        raise RuntimeError(f"staging API {response.status_code}: {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--wage-record", type=Path, required=True)
    parser.add_argument("--conflicting-wage-record", type=Path, required=True)
    parser.add_argument("--scanned-notice", type=Path, required=True)
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    api_base = os.getenv("STAGING_API_BASE", "").strip()
    token = os.getenv("STAGING_API_TOKEN", "").strip()
    if not api_base or not token:
        raise RuntimeError("STAGING_API_BASE and STAGING_API_TOKEN are required")
    paths = (
        args.contract,
        args.wage_record,
        args.conflicting_wage_record,
        args.scanned_notice,
    )
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    runner = StagingCaseRunner(api_base=api_base, token=token)
    try:
        result = await runner.run(
            contract=args.contract,
            wage_record=args.wage_record,
            conflicting_wage_record=args.conflicting_wage_record,
            scanned_notice=args.scanned_notice,
        )
    finally:
        await runner.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
