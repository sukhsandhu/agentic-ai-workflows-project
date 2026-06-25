from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / "config" / "agent_policy.json").read_text(encoding="utf-8"))
MEMORY_PATH = ROOT / "outputs" / "agent_memory.json"
LOG_PATH = ROOT / "outputs" / "execution_log.json"


@dataclass
class AgentMemory:
    reviewed_cases: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls) -> "AgentMemory":
        if MEMORY_PATH.exists():
            return cls(**json.loads(MEMORY_PATH.read_text(encoding="utf-8")))
        return cls()

    def save(self) -> None:
        MEMORY_PATH.parent.mkdir(exist_ok=True)
        MEMORY_PATH.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


def data_quality_tool(case: dict[str, Any]) -> dict[str, Any]:
    missing = case.get("missing_fields", [])
    score = min(1.0, 0.18 * len(missing))
    return {"missing_count": len(missing), "quality_risk": round(score, 3), "missing_fields": missing}


def policy_lookup_tool(case: dict[str, Any]) -> dict[str, Any]:
    if case.get("patient_facing"):
        return {"constraint": "human_approval_required", "reason": "patient-facing recommendation"}
    return {"constraint": "internal_review_allowed", "reason": "internal operations case"}


def priority_tool(case: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    base = float(case.get("risk_score", 0.0))
    confidence_gap = max(0.0, 0.7 - float(case.get("model_confidence", 0.0)))
    priority_score = min(1.0, base + quality["quality_risk"] + confidence_gap)
    return {"priority_score": round(priority_score, 3)}


class ClinicalOperationsTriageAgent:
    def __init__(self) -> None:
        self.memory = AgentMemory.load()
        self.policy = POLICY

    def validate(self, case: dict[str, Any]) -> list[str]:
        return [field for field in self.policy["required_fields"] if field not in case]

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        trace = [{"step": "initialize", "case_id": case.get("case_id", "unknown")}]
        missing_required = self.validate(case)
        if missing_required:
            decision = "manual_data_quality_review"
            result = {
                "case_id": case.get("case_id", "unknown"),
                "decision": decision,
                "reason": f"Missing required fields: {missing_required}",
                "trace": trace + [{"step": "fallback", "missing_required": missing_required}],
            }
            self.memory.reviewed_cases.append(result)
            self.memory.save()
            return result

        quality = data_quality_tool(case)
        trace.append({"step": "tool:data_quality", "observation": quality})
        policy = policy_lookup_tool(case)
        trace.append({"step": "tool:policy_lookup", "observation": policy})
        priority = priority_tool(case, quality)
        trace.append({"step": "tool:priority", "observation": priority})

        if policy["constraint"] == "human_approval_required":
            decision = "human_review_required"
            reason = "Patient-facing case cannot be finalized automatically."
        elif priority["priority_score"] >= self.policy["risk_thresholds"]["human_review"]:
            decision = "expedited_human_review"
            reason = "Combined risk and data-quality signals exceed review threshold."
        elif quality["quality_risk"] >= self.policy["risk_thresholds"]["manual_data_quality_review"]:
            decision = "manual_data_quality_review"
            reason = "Missing fields create uncertainty in the automated workflow."
        else:
            decision = "standard_queue"
            reason = "Signals are within operational thresholds."

        result = {
            "case_id": case["case_id"],
            "decision": decision,
            "reason": reason,
            "priority_score": priority["priority_score"],
            "quality_risk": quality["quality_risk"],
            "trace": trace,
        }
        self.memory.reviewed_cases.append(result)
        self.memory.save()
        return result


def run_demo() -> list[dict[str, Any]]:
    cases = [
        {"case_id": "CASE-001", "missing_fields": [], "model_confidence": 0.91, "risk_score": 0.21, "patient_facing": False},
        {"case_id": "CASE-002", "missing_fields": ["recent_lab_date", "follow_up_status"], "model_confidence": 0.63, "risk_score": 0.44, "patient_facing": False},
        {"case_id": "CASE-003", "missing_fields": [], "model_confidence": 0.82, "risk_score": 0.74, "patient_facing": True},
        {"case_id": "CASE-004", "missing_fields": ["model_confidence"], "risk_score": 0.33, "patient_facing": False},
    ]
    agent = ClinicalOperationsTriageAgent()
    outputs = [agent.run_case(case) for case in cases]
    LOG_PATH.parent.mkdir(exist_ok=True)
    LOG_PATH.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
    return outputs


if __name__ == "__main__":
    for item in run_demo():
        print(f"{item['case_id']}: {item['decision']} - {item['reason']}")
