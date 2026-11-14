import pytest

from echosim.domain.accounts import Fault
from echosim.domain.chapters import check_version, editable, evidence


@pytest.mark.parametrize("decision", ["discarded", "ineligible", "checking"])
def test_closed_result_rejects_generation(decision):
    with pytest.raises(Fault):
        editable({"state": "succeeded", "decision": decision})


def test_replacement_cannot_overwrite_concurrent_edit():
    with pytest.raises(Fault, match="CHAPTER_STALE"):
        check_version({"version": 2}, 1)


def test_changed_recipient_evidence_excludes_original_recipient():
    body = "甲把钥匙递给丙。"
    rows = evidence([{"text": body, "visibility": ["1", "3"]}], body)
    assert rows == [{"text": body, "visibility": ["1", "3"]}]
    with pytest.raises(ValueError):
        evidence([{"text": "乙收到了钥匙。", "visibility": ["2"]}], body)
