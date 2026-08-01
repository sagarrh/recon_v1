from aivc.database.schema_audit import TABLE_REQUIREMENTS, assess_columns


def test_assess_columns_reports_missing_contract_fields() -> None:
    discovered = {table: set(columns) for table, columns in TABLE_REQUIREMENTS.items()}
    discovered["ai_monitoring"].remove("request_payload")

    result = assess_columns(discovered)

    assert result["clients"]["required_columns_present"] is True
    assert result["ai_monitoring"]["required_columns_present"] is False
    assert result["ai_monitoring"]["missing_columns"] == ["request_payload"]


def test_assess_columns_marks_absent_table() -> None:
    result = assess_columns({})

    assert result["clients"]["present"] is False
    assert result["clients"]["column_count"] == 0
