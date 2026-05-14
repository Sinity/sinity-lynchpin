from lynchpin.sources.observability_catalog import observability_input_by_id, observability_inputs


def test_observability_catalog_identifies_canonical_machine_input():
    by_id = {item.id: item for item in observability_inputs()}

    machine = by_id["machine.telemetry"]
    assert machine.integration_state == "canonical"
    assert machine.substrate_table == "machine_metric_sample; machine_service_state; machine_network_sample"
    assert "psi" in machine.state_dimensions
    assert "network_link_quality" in machine.state_dimensions


def test_observability_catalog_keeps_operational_views_out_of_canonical_data():
    below = observability_input_by_id("machine.below")
    observe = observability_input_by_id("machine.sinnix_observe")

    assert below.layer == "operational_view"
    assert below.substrate_table is None
    assert "bounded windows" in below.next_action
    assert observe.layer == "operational_view"
    assert observe.retention == "not a canonical dataset"


def test_observability_catalog_tracks_pending_promotions():
    pending = {
        item.id
        for item in observability_inputs()
        if item.integration_state == "promote_pending"
    }

    assert "sinex.self_observation" in pending
    assert "machine.experiment_run" not in pending
