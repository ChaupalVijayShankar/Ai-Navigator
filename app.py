import json
import os
import time
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

GEMINI_MODEL_NAME = "gemini-3.1-flash-lite"

CAREER_ROLES = {
    "AI Engineer",
    "AI/ML Engineer",
    "Machine Learning Engineer",
    "Data Scientist",
    "Data Analyst",
    "Python Developer",
    "Full Stack Developer",
    "Web Developer",
    "Cybersecurity Analyst",
    "Cloud Engineer",
    "Other",
}

app = FastAPI(title="AI Career Navigator - Python Backend")


class RoadmapRequest(BaseModel):
    studentName: Optional[str] = ""
    currentSkills: str = Field(..., min_length=2, max_length=2000)
    targetRole: str = Field(..., min_length=2, max_length=100)
    customRole: Optional[str] = ""
    experienceLevel: Literal["Beginner", "Intermediate", "Advanced"]
    studyHoursPerWeek: int = Field(..., ge=1, le=80)
    careerGoal: str = Field(..., min_length=4, max_length=1000)


class MentorRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    targetRole: Optional[str] = "Software / Tech Career"
    experienceLevel: Optional[str] = "Student"
    currentSkills: Optional[str] = ""
    studyHoursPerWeek: Optional[int] = 10
    readinessPercentage: Optional[int] = None


def resolve_server_gemini_key():
    key_exists = "GEMINI_API_KEY" in os.environ and os.environ.get("GEMINI_API_KEY") is not None
    raw = os.environ.get("GEMINI_API_KEY")
    if not isinstance(raw, str):
        return None, "missing", key_exists, False, False

    normalized = raw.strip()
    if (normalized.startswith('"') and normalized.endswith('"')) or (
        normalized.startswith("'") and normalized.endswith("'")
    ):
        normalized = normalized[1:-1].strip()

    if not normalized:
        return None, "missing", key_exists, True, False

    upper = normalized.upper()
    if (
        upper in {"MY_GEMINI_API_KEY", "YOUR_GEMINI_API_KEY", "YOUR_API_KEY"}
        or "REPLACE_WITH" in upper
        or len(normalized) < 10
    ):
        return None, "placeholder", key_exists, False, True

    return normalized, None, True, False, False


def extract_json_object(raw_text: str) -> str:
    trimmed = raw_text.strip()
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return trimmed


def get_missing_key_message(key_issue: Optional[str], vercel_env: str) -> str:
    if key_issue == "placeholder":
        return (
            "GEMINI_API_KEY is still set to a placeholder value on the server. "
            "Replace it with a real Gemini API key in your environment variables and redeploy."
        )
    env_hint = (
        ' Note: this deployment is running in the Vercel "preview" environment—verify GEMINI_API_KEY is enabled for Preview as well as Production.'
        if vercel_env == "preview"
        else ""
    )
    return (
        "Gemini API key is missing or not configured on the server. "
        f"Please add GEMINI_API_KEY in your Vercel Project Settings -> Environment Variables and redeploy without build cache.{env_hint}"
    )


@app.get("/api/health")
def health(verify: Optional[str] = None):
    api_key, key_issue, key_exists, is_empty, is_placeholder = resolve_server_gemini_key()
    runtime = "vercel" if os.environ.get("VERCEL") else "node"
    vercel_env = os.environ.get("VERCEL_ENV") or ("unknown" if runtime == "vercel" else "local")

    sdk_initialized = False
    client = None
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            sdk_initialized = True
        except Exception:
            sdk_initialized = False

    api_connected = None
    api_status = "not_checked" if api_key else "missing_key"

    if verify == "1" and client and sdk_initialized:
        try:
            ping = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents="Reply with OK.",
            )
            api_connected = bool((ping.text or "").strip())
            api_status = "connected" if api_connected else "network_error"
        except Exception as exc:
            api_connected = False
            msg = str(exc).lower()
            if "401" in msg or "403" in msg or "api_key_invalid" in msg:
                api_status = "auth_error"
            elif "429" in msg or "quota" in msg or "resource_exhausted" in msg:
                api_status = "quota_error"
            elif "404" in msg or "not_found" in msg:
                api_status = "model_error"
            else:
                api_status = "network_error"

    return {
        "success": True,
        "data": {
            "status": "ok",
            "model": GEMINI_MODEL_NAME,
            "aiConfigured": bool(api_key and sdk_initialized),
            "keyIssue": key_issue,
            "runtime": runtime,
            "diagnostics": {
                "keyExists": key_exists,
                "isEmptyString": is_empty,
                "isPlaceholder": is_placeholder,
                "sdkInitialized": sdk_initialized,
                "apiConnected": api_connected,
                "apiStatus": api_status,
                "vercelEnv": vercel_env,
            },
        },
    }


@app.post("/api/roadmap")
def generate_roadmap(payload: RoadmapRequest):
    if payload.targetRole not in CAREER_ROLES:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Please select a valid target career role."},
        )

    resolved_role = (
        (payload.customRole or "").strip()
        if payload.targetRole == "Other"
        else payload.targetRole.strip()
    )
    if len(resolved_role) < 2:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": 'Please specify your target career role when selecting "Other".'},
        )

    api_key, key_issue, *_ = resolve_server_gemini_key()
    vercel_env = os.environ.get("VERCEL_ENV") or ("unknown" if os.environ.get("VERCEL") else "local")
    if not api_key:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": get_missing_key_message(key_issue, vercel_env)},
        )

    student_name = (payload.studentName or "").strip()[:80] or "Learner"
    client = genai.Client(api_key=api_key)

    system_instruction = (
        "You are a senior technical career strategist and curriculum architect. "
        "Analyze the student profile accurately and return strictly valid JSON matching the requested structure. "
        "Calibrate the 8-week roadmap realistically to the student available study hours per week and experience level. "
        "Ensure weeklyRoadmap contains exactly 8 progressive weeks (Week 1 through Week 8). "
        "Ensure projects contains exactly 3 practical portfolio mini-projects with required technologies and expected learning outcomes. "
        "Ensure careerTips contains exactly 5 concrete, actionable career improvement tips."
    )

    prompt = (
        f"Student Name: {student_name}\n"
        f"Target Career Role: {resolved_role}\n"
        f"Current Experience Level: {payload.experienceLevel}\n"
        f"Available Study Hours Per Week: {payload.studyHoursPerWeek} hours/week\n"
        f"Current Skills: {payload.currentSkills}\n"
        f"Career Goal: {payload.careerGoal}\n\n"
        "Return strictly valid JSON with keys:\n"
        "- readinessPercentage (integer 0-100)\n"
        "- readinessLabel (string: Foundational | Developing | Competitive | Job-Ready)\n"
        "- readinessSummary (string)\n"
        "- existingSkills (list of {skill, relevance})\n"
        "- missingSkills (list of {skill, priority: Essential|High|Medium, reason})\n"
        "- recommendedSkills (list of {skill, category, whyLearn})\n"
        "- weeklyRoadmap (8 items: {week, title, focusArea, estimatedHours, topics, practiceTask, milestone})\n"
        "- projects (3 items: {title, difficulty, summary, skillsApplied, deliverable})\n"
        "- careerTips (5 items: {title, advice})"
    )

    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
            )
            raw_text = (response.text or "").strip()
            if not raw_text:
                raise ValueError("Invalid empty response received from Gemini AI.")

            parsed = json.loads(extract_json_object(raw_text))
            weekly_roadmap = parsed.get("weeklyRoadmap") or []
            projects = parsed.get("projects") or []
            if len(weekly_roadmap) < 8 or len(projects) < 3:
                raise ValueError("Incomplete roadmap structure received from Gemini AI.")

            readiness = max(0, min(100, round(float(parsed.get("readinessPercentage", 0)))))
            result = {
                "studentName": student_name,
                "resolvedTargetRole": resolved_role,
                "experienceLevel": payload.experienceLevel,
                "studyHoursPerWeek": payload.studyHoursPerWeek,
                "careerGoal": payload.careerGoal,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "readinessPercentage": readiness,
                "readinessLabel": parsed.get("readinessLabel")
                or ("Competitive" if readiness >= 75 else "Developing" if readiness >= 45 else "Foundational"),
                "readinessSummary": parsed.get("readinessSummary", ""),
                "existingSkills": parsed.get("existingSkills", []),
                "missingSkills": parsed.get("missingSkills", []),
                "recommendedSkills": parsed.get("recommendedSkills", []),
                "weeklyRoadmap": weekly_roadmap[:8],
                "projects": projects[:3],
                "careerTips": (parsed.get("careerTips") or [])[:5],
            }
            return {"success": True, "data": result}
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.7 * (2 ** (attempt - 1)))
                continue

    return JSONResponse(
        status_code=502,
        content={
            "success": False,
            "error": "Unable to complete roadmap generation with Gemini AI. Please try again.",
            "details": str(last_error),
        },
    )


@app.post("/api/mentor")
def career_mentor(payload: MentorRequest):
    api_key, key_issue, *_ = resolve_server_gemini_key()
    vercel_env = os.environ.get("VERCEL_ENV") or ("unknown" if os.environ.get("VERCEL") else "local")
    if not api_key:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": get_missing_key_message(key_issue, vercel_env)},
        )

    client = genai.Client(api_key=api_key)
    readiness = f"{payload.readinessPercentage}%" if payload.readinessPercentage is not None else "Not yet assessed"
    prompt = (
        "Student Context:\n"
        f"- Target Role: {payload.targetRole}\n"
        f"- Experience Level: {payload.experienceLevel}\n"
        f"- Weekly Study Hours: {payload.studyHoursPerWeek} hrs/week\n"
        f"- Current Skills: {(payload.currentSkills or '').strip() or 'Not specified yet'}\n"
        f"- Readiness Assessment: {readiness}\n\n"
        f"Student Question: {payload.question.strip()}"
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are an experienced AI Career Mentor helping students and career switchers break into technical roles. "
                    "Give concise, practical, encouraging, and directly actionable answers."
                )
            ),
        )
        answer = (response.text or "").strip()
        if not answer:
            raise ValueError("Invalid empty response received from Gemini AI.")
        return {"success": True, "data": {"answer": answer}}
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "error": "Unable to complete career mentor with Gemini AI. Please try again.",
                "details": str(exc),
            },
        )
