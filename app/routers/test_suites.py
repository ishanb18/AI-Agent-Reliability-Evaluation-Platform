"""
Test Suites Router — CRUD, file upload, seed generation, and auto-generation endpoints.

Endpoints:
  POST   /test-suites                              Create a new empty test suite
  GET    /test-suites                              List all test suites
  GET    /test-suites/{suite_id}                   Get suite + all test cases
  POST   /test-suites/{suite_id}/cases             Add a single test case
  POST   /test-suites/{suite_id}/upload            Bulk upload via JSON/CSV
  DELETE /test-suites/{suite_id}                   Delete suite and all cases
  POST   /test-suites/seed                         Generate 35 seed test cases
  POST   /test-suites/{suite_id}/generate          Auto-generate test cases (Day 5)
  GET    /test-suites/{suite_id}/generated          List pending review cases (Day 5)
  POST   /test-suites/{suite_id}/generated/approve  Approve/reject generated cases (Day 5)
"""

import csv
import json
import io
from typing import Optional, List
from collections import Counter

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.test_suite import TestSuite, TestCase
from app.models.agent import Agent
from app.schemas.test_suite import (
    TestSuiteCreate,
    TestSuiteResponse,
    TestSuiteListResponse,
    TestCaseCreate,
    TestCaseResponse,
    BulkUploadResponse,
    UploadError,
    SeedResponse,
)
from app.schemas.analysis import (
    GenerateTestsRequest,
    GenerateTestsResponse,
    GeneratedCasePreview,
    ApproveTestsRequest,
    ApproveTestsResponse,
)
from app.seed.test_cases import SEED_TEST_CASES
from app.evaluation import security_tests, test_generator
from app.providers import ModelGateway

log = structlog.get_logger()
router = APIRouter()


# ── Helper: ORM → Response conversion ────────────────────────────────────────

def _case_to_response(case: TestCase) -> TestCaseResponse:
    """Convert TestCase ORM → Pydantic response, deserializing expected_tools JSON."""
    return TestCaseResponse(
        id=case.id,
        suite_id=case.suite_id,
        input=case.input,
        expected_answer=case.expected_answer,
        expected_tools=case.get_expected_tools_list() or None,
        category=case.category,
        risk_level=case.risk_level,
        source=case.source,
        status=getattr(case, 'status', 'active'),
        created_at=case.created_at,
    )


# Module-level gateway singleton for test generation
_gateway = None


def _get_gateway():
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


def _suite_to_response(suite: TestSuite) -> TestSuiteResponse:
    """Convert TestSuite ORM → Pydantic response with nested cases."""
    cases = [_case_to_response(c) for c in suite.test_cases]
    return TestSuiteResponse(
        id=suite.id,
        name=suite.name,
        description=suite.description,
        agent_id=suite.agent_id,
        test_cases=cases,
        case_count=len(cases),
        created_at=suite.created_at,
    )


def _suite_to_list_response(suite: TestSuite) -> TestSuiteListResponse:
    """Lightweight suite response for list view (no nested cases)."""
    return TestSuiteListResponse(
        id=suite.id,
        name=suite.name,
        description=suite.description,
        agent_id=suite.agent_id,
        case_count=len(suite.test_cases),
        created_at=suite.created_at,
    )


# ── Helper: Create a TestCase ORM object from validated data ──────────────────

def _create_test_case(suite_id: int, data: dict) -> TestCase:
    """Create a TestCase ORM object from a dict of validated fields."""
    case = TestCase(
        suite_id=suite_id,
        input=data["input"],
        expected_answer=data.get("expected_answer"),
        category=data.get("category", "general"),
        risk_level=data.get("risk_level", "low"),
        source=data.get("source", "user"),
    )
    # Serialize expected_tools list → JSON string
    tools = data.get("expected_tools")
    if tools:
        if isinstance(tools, str):
            # User might pass tools as a JSON string in CSV
            case.expected_tools = tools
        else:
            case.set_expected_tools_list(tools)
    return case


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/seed", response_model=SeedResponse, status_code=201)
def create_seed_suite(db: Session = Depends(get_db)):
    """
    Generate a seed test suite pre-populated with 35 curated test cases
    spanning 5 evaluation categories: general, rag, tool_use, instruction, security.

    This gives users a ready-made test suite to immediately evaluate their agent
    without writing test cases from scratch.

    NOTE: This endpoint must be defined BEFORE /{suite_id} routes to prevent
    FastAPI from interpreting "seed" as a suite_id parameter.
    """
    log.info("creating seed test suite")

    # Create the suite
    suite = TestSuite(
        name="Seed Test Suite — Platform Defaults",
        description=(
            "Pre-built evaluation suite with 35 curated test cases across "
            "5 categories: General QA, RAG Retrieval, Tool Use, "
            "Instruction Following, and Security. Use this for quick "
            "initial evaluation of any agent."
        ),
    )
    db.add(suite)
    db.flush()  # flush to get suite.id before creating cases

    # Create all seed test cases
    category_counts = Counter()
    for seed_data in SEED_TEST_CASES:
        case = _create_test_case(suite.id, seed_data)
        db.add(case)
        category_counts[seed_data["category"]] += 1

    db.commit()
    db.refresh(suite)

    log.info(
        "seed suite created",
        suite_id=suite.id,
        total_cases=len(SEED_TEST_CASES),
        categories=dict(category_counts),
    )

    return SeedResponse(
        suite_id=suite.id,
        suite_name=suite.name,
        cases_created=len(SEED_TEST_CASES),
        categories=dict(category_counts),
    )


@router.post("", response_model=TestSuiteResponse, status_code=201)
def create_suite(suite_data: TestSuiteCreate, db: Session = Depends(get_db)):
    """Create a new empty test suite. Add cases later via /cases or /upload."""
    # Validate agent_id if provided
    if suite_data.agent_id:
        agent = db.query(Agent).filter(Agent.id == suite_data.agent_id).first()
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent with id={suite_data.agent_id} not found. Create the agent first.",
            )

    suite = TestSuite(
        name=suite_data.name,
        description=suite_data.description,
        agent_id=suite_data.agent_id,
    )
    db.add(suite)
    db.commit()
    db.refresh(suite)

    log.info("test suite created", suite_id=suite.id, name=suite.name)
    return _suite_to_response(suite)


@router.get("", response_model=List[TestSuiteListResponse])
def list_suites(
    agent_id: Optional[int] = Query(default=None, description="Filter suites by agent ID"),
    db: Session = Depends(get_db),
):
    """List all test suites. Optionally filter by agent_id."""
    query = db.query(TestSuite)
    if agent_id is not None:
        query = query.filter(TestSuite.agent_id == agent_id)
    suites = query.order_by(TestSuite.created_at.desc()).all()
    return [_suite_to_list_response(s) for s in suites]


@router.get("/{suite_id}", response_model=TestSuiteResponse)
def get_suite(suite_id: int, db: Session = Depends(get_db)):
    """Get a test suite by ID, including all its test cases."""
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")
    return _suite_to_response(suite)


@router.post("/{suite_id}/cases", response_model=TestCaseResponse, status_code=201)
def add_test_case(suite_id: int, case_data: TestCaseCreate, db: Session = Depends(get_db)):
    """Add a single test case to an existing suite."""
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    case = _create_test_case(suite_id, case_data.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)

    log.info("test case added", suite_id=suite_id, case_id=case.id, category=case.category)
    return _case_to_response(case)


@router.post("/{suite_id}/upload", response_model=BulkUploadResponse)
def upload_test_cases(
    suite_id: int,
    file: UploadFile = File(..., description="JSON or CSV file containing test cases"),
    db: Session = Depends(get_db),
):
    """
    Bulk upload test cases from a JSON or CSV file.

    JSON format (array of objects):
      [
        {"input": "...", "expected_answer": "...", "category": "general", "risk_level": "low"},
        {"input": "...", "expected_tools": ["tool1", "tool2"], "category": "tool_use"}
      ]

    CSV format (with header row):
      input,expected_answer,expected_tools,category,risk_level
      "Can I cancel?","Yes you can","[""cancel_order""]",general,low
    """
    # Verify suite exists
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    # Read file content
    content = file.file.read()
    filename = file.filename or ""

    # Determine file type
    if filename.lower().endswith(".json"):
        rows = _parse_json_upload(content)
    elif filename.lower().endswith(".csv"):
        rows = _parse_csv_upload(content)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: '{filename}'. Upload a .json or .csv file.",
        )

    # Process each row
    created = 0
    errors: List[UploadError] = []

    for idx, row in enumerate(rows, start=1):
        try:
            # Validate with Pydantic schema
            validated = TestCaseCreate(**row)
            case = _create_test_case(suite_id, validated.model_dump())
            db.add(case)
            created += 1
        except Exception as e:
            errors.append(UploadError(row=idx, error=str(e)))

    db.commit()

    log.info(
        "bulk upload complete",
        suite_id=suite_id,
        file=filename,
        created=created,
        errors=len(errors),
    )

    return BulkUploadResponse(
        suite_id=suite_id,
        created=created,
        errors=len(errors),
        error_details=errors,
    )


@router.delete("/{suite_id}", status_code=200)
def delete_suite(suite_id: int, db: Session = Depends(get_db)):
    """
    Delete a test suite and all its test cases.
    cascade="all, delete-orphan" on the relationship handles case deletion automatically.
    """
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    suite_name = suite.name
    case_count = len(suite.test_cases)
    db.delete(suite)
    db.commit()

    log.info("test suite deleted", suite_id=suite_id, cases_deleted=case_count)
    return {
        "detail": f"Test suite '{suite_name}' (id={suite_id}) deleted with {case_count} test cases",
    }


# ── File Parsing Helpers ──────────────────────────────────────────────────────

def _parse_json_upload(content: bytes) -> List[dict]:
    """Parse uploaded JSON file into list of row dicts."""
    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")

    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail="JSON file must contain a top-level array of test case objects.",
        )
    return data


def _parse_csv_upload(content: bytes) -> List[dict]:
    """
    Parse uploaded CSV file into list of row dicts.
    Handles expected_tools column: if it looks like a JSON array string, parse it.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Cannot decode CSV file: {str(e)}")

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Clean up whitespace in keys
        cleaned = {k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}

        # Parse expected_tools: CSV stores it as a string, try to parse as JSON list
        if "expected_tools" in cleaned and cleaned["expected_tools"]:
            try:
                cleaned["expected_tools"] = json.loads(cleaned["expected_tools"])
            except json.JSONDecodeError:
                # If not valid JSON, treat as comma-separated string
                cleaned["expected_tools"] = [
                    t.strip() for t in cleaned["expected_tools"].split(",") if t.strip()
                ]
        else:
            cleaned["expected_tools"] = None

        rows.append(cleaned)
    return rows


# ── Day 5: Auto Test Case Generation Endpoints ───────────────────────────────

@router.post("/{suite_id}/generate", response_model=GenerateTestsResponse, status_code=201)
def generate_test_cases(
    suite_id: int,
    request: GenerateTestsRequest,
    db: Session = Depends(get_db),
):
    """
    Auto-generate test cases using LLM or hardcoded templates.

    Generated cases are saved with status="pending_review" — they are NOT
    included in evaluation runs until explicitly approved.

    Modes:
      - edge: unusual but valid boundary condition inputs
      - adversarial: tricky, misleading inputs to expose failures
      - security: hardcoded templates + LLM-generated domain-specific probes

    Flow: generate → review via GET /generated → approve via POST /generated/approve
    """
    # Verify suite exists
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    # Get agent info for context (if suite is linked to an agent)
    agent = None
    agent_components = []
    agent_description = request.description or ""
    if suite.agent_id:
        agent = db.query(Agent).filter(Agent.id == suite.agent_id).first()
        if agent:
            agent_components = agent.get_components_list()
            if not agent_description:
                agent_description = agent.description or agent.name

    # Get existing cases as context for the generator
    existing_cases = [
        {"input": c.input, "expected_answer": c.expected_answer, "category": c.category}
        for c in suite.test_cases[:10]
    ]

    generated_cases: List[dict] = []

    if request.mode == "security":
        # Step 1: Hardcoded security templates (always available, zero cost)
        hardcoded = security_tests.get_security_tests(agent_components)
        for case_data in hardcoded:
            case_data["status"] = "pending_review"
        generated_cases.extend(hardcoded)

        # Step 2: LLM-generated domain-specific security cases
        if agent_description:
            try:
                gateway = _get_gateway()
                llm_cases = test_generator.generate_security_cases(
                    agent_components=agent_components,
                    agent_description=agent_description,
                    gateway=gateway,
                    count=request.count,
                )
                generated_cases.extend(llm_cases)
            except Exception as e:
                log.warning("LLM security generation failed, using only hardcoded", error=str(e))

    elif request.mode == "edge":
        try:
            gateway = _get_gateway()
            generated_cases = test_generator.generate_edge_cases(
                existing_cases=existing_cases,
                agent_description=agent_description,
                gateway=gateway,
                count=request.count,
            )
        except Exception as e:
            log.error("edge case generation failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Test generation failed: {str(e)}")

    elif request.mode == "adversarial":
        try:
            gateway = _get_gateway()
            generated_cases = test_generator.generate_adversarial_cases(
                existing_cases=existing_cases,
                agent_description=agent_description,
                gateway=gateway,
                count=request.count,
            )
        except Exception as e:
            log.error("adversarial case generation failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Test generation failed: {str(e)}")

    if not generated_cases:
        raise HTTPException(
            status_code=500,
            detail="No test cases could be generated. Check LLM provider availability.",
        )

    # Save generated cases to DB with status="pending_review"
    created_cases = []
    for case_data in generated_cases:
        case = TestCase(
            suite_id=suite_id,
            input=case_data.get("input", ""),
            expected_answer=case_data.get("expected_answer"),
            category=case_data.get("category", "general"),
            risk_level=case_data.get("risk_level", "medium"),
            source=case_data.get("source", "generated"),
            status="pending_review",
        )
        # Handle expected_tools
        tools = case_data.get("expected_tools")
        if tools and isinstance(tools, list):
            case.set_expected_tools_list(tools)

        db.add(case)
        db.flush()  # get the ID
        created_cases.append(case)

    db.commit()

    log.info(
        "test cases generated",
        suite_id=suite_id,
        mode=request.mode,
        count=len(created_cases),
    )

    return GenerateTestsResponse(
        suite_id=suite_id,
        mode=request.mode,
        generated_count=len(created_cases),
        cases=[
            GeneratedCasePreview(
                id=c.id,
                input=c.input,
                expected_answer=c.expected_answer,
                category=c.category,
                risk_level=c.risk_level,
                source=c.source,
                status="pending_review",
            )
            for c in created_cases
        ],
        message=(
            f"{len(created_cases)} test cases generated with status='pending_review'. "
            f"Review them via GET /test-suites/{suite_id}/generated, "
            f"then approve via POST /test-suites/{suite_id}/generated/approve"
        ),
    )


@router.get("/{suite_id}/generated", response_model=List[TestCaseResponse])
def list_generated_cases(
    suite_id: int,
    db: Session = Depends(get_db),
):
    """
    List all test cases with status='pending_review' in this suite.

    This endpoint is used to review auto-generated test cases before approving them.
    The user can read through each case and decide which ones to approve.
    """
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    pending = db.query(TestCase).filter(
        TestCase.suite_id == suite_id,
        TestCase.status == "pending_review",
    ).all()

    return [_case_to_response(c) for c in pending]


@router.post("/{suite_id}/generated/approve", response_model=ApproveTestsResponse)
def approve_generated_cases(
    suite_id: int,
    request: ApproveTestsRequest,
    db: Session = Depends(get_db),
):
    """
    Approve or reject auto-generated test cases.

    Approved cases: status changes from 'pending_review' → 'active' (included in eval runs).
    If reject_remaining=True: all OTHER pending_review cases in this suite are marked 'rejected'.
    If reject_remaining=False: un-approved cases stay as 'pending_review' for later review.
    """
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
    if not suite:
        raise HTTPException(status_code=404, detail=f"Test suite with id={suite_id} not found")

    # Get all pending_review cases in this suite
    pending_cases = db.query(TestCase).filter(
        TestCase.suite_id == suite_id,
        TestCase.status == "pending_review",
    ).all()

    if not pending_cases:
        raise HTTPException(
            status_code=404,
            detail=f"No pending review cases found in suite {suite_id}",
        )

    approved_count = 0
    rejected_count = 0
    still_pending = 0

    approved_ids = set(request.case_ids)

    for case in pending_cases:
        if case.id in approved_ids:
            case.status = "active"
            approved_count += 1
        elif request.reject_remaining:
            case.status = "rejected"
            rejected_count += 1
        else:
            still_pending += 1

    db.commit()

    log.info(
        "generated cases reviewed",
        suite_id=suite_id,
        approved=approved_count,
        rejected=rejected_count,
        still_pending=still_pending,
    )

    return ApproveTestsResponse(
        approved=approved_count,
        rejected=rejected_count,
        still_pending=still_pending,
    )

