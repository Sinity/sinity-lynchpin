from lynchpin.sources.observability_catalog import observability_input_by_id, observability_inputs
from lynchpin.substrate.schema import DDL_STATEMENTS


def _substrate_tables() -> set[str]:
    tables: set[str] = set()
    for statement in DDL_STATEMENTS:
        marker = "CREATE TABLE "
        if marker not in statement:
            continue
        tail = statement.split(marker, 1)[1].strip()
        tables.add(tail.split("(", 1)[0].strip())
    return tables


def test_observability_catalog_identifies_canonical_machine_input():
    by_id = {item.id: item for item in observability_inputs()}

    machine = by_id["machine.telemetry"]
    assert machine.integration_state == "canonical"
    assert machine.substrate_table == "machine_metric_sample; machine_gpu_sample; machine_service_state; machine_network_sample"
    assert "psi" in machine.state_dimensions
    assert "gpu_high_frequency" in machine.state_dimensions
    assert "network_link_quality" in machine.state_dimensions


def test_observability_catalog_keeps_operational_views_out_of_canonical_data():
    below = observability_input_by_id("machine.below")
    observe = observability_input_by_id("machine.sinnix_observe")

    assert below.layer == "operational_view"
    assert below.substrate_table is None
    assert "bounded windows" in below.next_action
    assert observe.layer == "operational_view"
    assert observe.path is not None
    assert observe.path.name == "sinnix-observe"
    assert observe.retention == "not a canonical dataset"


def test_observability_catalog_tracks_sinnix_inventory_as_reference_only():
    contract = observability_input_by_id("machine.sinnix_runtime_inventory")

    assert contract.integration_state == "reference_only"
    assert contract.substrate_table is None
    assert contract.path is not None
    assert contract.path.name == "runtime-inventory.json"


def test_observability_catalog_tracks_pending_promotions():
    sinex = observability_input_by_id("sinex.self_observation")
    pending = {
        item.id
        for item in observability_inputs()
        if item.integration_state == "promote_pending"
    }

    assert "sinex.self_observation" in pending
    assert sinex.substrate_table is None
    assert "add a Lynchpin source and substrate table" in sinex.next_action
    assert "machine.experiment_run" not in pending


def test_observability_catalog_references_existing_substrate_tables():
    tables = _substrate_tables()

    for item in observability_inputs():
        if item.substrate_table is None:
            continue
        for table in item.substrate_table.split(";"):
            assert table.strip() in tables
