import pytest

from app.schemas import Evidence
from app.services.dependent_hybrid import (
    PolicyThresholdError,
    is_dependent_revenue_request,
    period_in_request,
    resolve_revenue_threshold,
)


def doc(passage, source="policy-1", evidence_id="e-policy"):
    return Evidence(
        source_type="document",
        source_id=source,
        evidence_id=evidence_id,
        title="Governed Revenue Policy",
        passage=passage,
    )


@pytest.mark.parametrize(
    "request, expected",
    [
        ("Which customers exceed the annual revenue threshold in our policy?", True),
        ("Compare revenue with the minimum specified in the document", True),
        ("What was the revenue last year?", False),
        ("Tell me the policy launch code", False),
        ("Show sales and orders", False),
    ],
)
def test_dependent_intent_is_explicit(request, expected):
    assert is_dependent_revenue_request(request) is expected


@pytest.mark.parametrize(
    "text,period,amount",
    [
        ("Annual revenue threshold: USD 300.00", "annual", "300.00"),
        ("Monthly revenue threshold is $1,250", "monthly", "1250"),
        ("Revenue cutoff: US$ 425", "unspecified", "425"),
        ("Minimum quarterly revenue: $600", "quarterly", "600"),
    ],
)
def test_resolves_currency_bound_document_span(text, period, amount):
    resolved = resolve_revenue_threshold([doc(text)])
    assert str(resolved.amount) == amount
    assert resolved.period == period
    assert resolved.citation == "[DOC 1]"
    assert resolved.source_id == "policy-1"
    assert resolved.evidence_id == "e-policy"
    assert resolved.matching_text == text


def test_repeated_consistent_policy_mentions_are_acceptable():
    chosen = resolve_revenue_threshold([
        doc("Annual revenue threshold: USD 300"),
        doc("Annual revenue threshold is US$300", "policy-2", "e2"),
    ], requested_period="annual")
    assert chosen.citation == "[DOC 1]"


@pytest.mark.parametrize(
    "evidence,period",
    [
        ([], "annual"),
        ([doc("Revenue last year was $300")], None),
        ([doc("Annual revenue threshold: 300")], "annual"),
        ([doc("Annual revenue threshold: $0")], "annual"),
        ([doc("Revenue threshold: $300")], "annual"),
        ([doc("Monthly revenue threshold: $300")], "annual"),
        ([doc("Annual revenue threshold: $300\nAnnual revenue threshold: $450")], "annual"),
        ([doc("Annual revenue threshold: $300\nMonthly revenue threshold: $300")], None),
        ([Evidence(source_type="structured_query", source_id="db", title="Data", passage="Annual revenue threshold: $300")], None),
    ],
)
def test_ambiguous_or_unverified_thresholds_fail_closed(evidence, period):
    with pytest.raises(PolicyThresholdError):
        resolve_revenue_threshold(evidence, requested_period=period)


def test_request_period_is_not_silently_rewritten():
    assert period_in_request("annual revenue threshold") == "annual"
    assert period_in_request("monthly revenue threshold") == "monthly"
    assert period_in_request("revenue threshold") is None
    with pytest.raises(PolicyThresholdError):
        period_in_request("annual and monthly revenue thresholds")



def test_verified_threshold_replaces_only_revenue_sql_identifier():
    from app.services.dependent_hybrid import (
        bind_document_threshold_sql,
        POLICY_SQL_MARKER,
    )
    threshold = resolve_revenue_threshold(
        [doc("Annual revenue threshold: USD 300")], requested_period="annual"
    )
    sql = bind_document_threshold_sql(
        "SELECT customer FROM customer_revenue WHERE annual_revenue > "
        + POLICY_SQL_MARKER,
        threshold,
        requested_period="annual",
        operator=">",
    )
    assert "annual_revenue > 300" in sql
    assert POLICY_SQL_MARKER not in sql


def test_requested_comparator_semantics():
    from app.services.dependent_hybrid import requested_threshold_operator
    assert requested_threshold_operator("Who exceeds the policy threshold?") == ">"
    assert requested_threshold_operator("Who meets the policy threshold?") == ">="
    with pytest.raises(PolicyThresholdError):
        requested_threshold_operator("Who meets or exceeds the threshold?")


@pytest.mark.parametrize("sql", [
    "SELECT customer FROM customer_revenue WHERE annual_revenue > 299",
    "SELECT customer FROM customer_revenue WHERE annual_revenue >= __OPENJM_POLICY_THRESHOLD__",
    "SELECT customer FROM customer_revenue WHERE monthly_revenue > __OPENJM_POLICY_THRESHOLD__",
    "SELECT customer FROM customer_revenue WHERE expenses > __OPENJM_POLICY_THRESHOLD__",
    "SELECT customer FROM customer_revenue WHERE annual_revenue+200 > __OPENJM_POLICY_THRESHOLD__",
    "SELECT customer FROM customer_revenue WHERE annual_revenue > __OPENJM_POLICY_THRESHOLD__ OR 1=1",
    "SELECT customer FROM customer_revenue WHERE annual_revenue > __OPENJM_POLICY_THRESHOLD__ AND EXISTS (SELECT 1)",
    "SELECT customer FROM customer_revenue WHERE annual_revenue > __OPENJM_POLICY_THRESHOLD__; DROP TABLE customer_revenue",
    "SELECT customer FROM customer_revenue WHERE annual_revenue > __OPENJM_POLICY_THRESHOLD__ UNION SELECT customer FROM customers",
    "DELETE FROM customer_revenue WHERE annual_revenue > __OPENJM_POLICY_THRESHOLD__",
])
def test_threshold_binding_rejects_semantic_substitution(sql):
    from app.services.dependent_hybrid import bind_document_threshold_sql
    threshold = resolve_revenue_threshold(
        [doc("Annual revenue threshold: USD 300")], requested_period="annual"
    )
    with pytest.raises(PolicyThresholdError):
        bind_document_threshold_sql(
            sql, threshold, requested_period="annual", operator=">"
        )


def test_different_currency_or_unqualified_dollar_is_not_merged():
    with pytest.raises(PolicyThresholdError):
        resolve_revenue_threshold([
            doc("Annual revenue threshold: USD 300"),
            doc("Annual revenue threshold: $300", "policy-2", "e2"),
        ])
    with pytest.raises(PolicyThresholdError):
        resolve_revenue_threshold([
            doc("Annual revenue threshold: USD 300"),
            doc("Annual revenue threshold: JMD 300", "policy-2", "e2"),
        ])


def test_explicit_currency_is_extracted_without_exchange_rate_assumption():
    assert resolve_revenue_threshold(
        [doc("Annual revenue threshold: USD 300")]
    ).currency == "USD"
    assert resolve_revenue_threshold(
        [doc("Annual revenue threshold: JMD 300")]
    ).currency == "JMD"
    assert resolve_revenue_threshold(
        [doc("Annual revenue threshold: $300")]
    ).currency is None
