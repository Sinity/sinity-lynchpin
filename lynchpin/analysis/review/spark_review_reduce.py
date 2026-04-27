"""Reduce SPARK packet review outputs into coverage and quality summaries."""

import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .._utils.io import load_json, resolve_analysis_path, save_json


def _result_files(results_dir):
    if not os.path.isdir(results_dir):
        return []
    out = []
    for root, _, files in os.walk(results_dir):
        for name in files:
            if name.endswith('.json'):
                out.append(os.path.join(root, name))
    out.sort()
    return out


def _load_results(results_dir):
    by_packet = {}
    invalid = []
    for fp in _result_files(results_dir):
        try:
            payload = load_json(fp)
        except Exception as exc:
            invalid.append({'file': fp, 'reason': f'invalid_json: {exc}'})
            continue

        packet_id = payload.get('packet_id')
        if not packet_id:
            packet_id = os.path.splitext(os.path.basename(fp))[0]

        if packet_id in by_packet:
            invalid.append({'file': fp, 'reason': f'duplicate_packet_id: {packet_id}'})
            continue

        by_packet[packet_id] = {
            'file': fp,
            'payload': payload,
        }
    return by_packet, invalid


def _is_valid_packet_result(payload):
    if not isinstance(payload, dict):
        return False, 'payload_not_object'
    if not payload.get('packet_id'):
        return False, 'missing_packet_id'
    if not isinstance(payload.get('units'), list):
        return False, 'missing_units_list'
    if payload.get('overall_confidence') not in {'low', 'medium', 'high'}:
        return False, 'invalid_overall_confidence'
    return True, None


def run_spark_review_reduce(packet_index_file, results_dir, out_file):
    packet_index = load_json(resolve_analysis_path(packet_index_file))
    expected_packets = packet_index.get('packets', [])
    expected_ids = {row['packet_id'] for row in expected_packets}

    resolved_results_dir = resolve_analysis_path(results_dir)
    by_packet, invalid_files = _load_results(resolved_results_dir)

    reviewed = []
    missing = []
    invalid_packets = []

    kind_counter = Counter()
    surface_counter = Counter()
    risk_counter = Counter()
    conf_counter = Counter()
    packet_conf_counter = Counter()
    per_eco = defaultdict(lambda: {'expected': 0, 'reviewed': 0, 'missing': 0})

    total_units = 0
    unclear_units = 0
    low_conf_units = 0
    units_without_evidence = 0

    packet_rows = []

    for packet in expected_packets:
        packet_id = packet['packet_id']
        eco = packet.get('ecosystem') or 'unknown'
        per_eco[eco]['expected'] += 1

        row = by_packet.get(packet_id)
        if not row:
            missing.append(packet_id)
            per_eco[eco]['missing'] += 1
            continue

        payload = row['payload']
        valid, reason = _is_valid_packet_result(payload)
        if not valid:
            invalid_packets.append({'packet_id': packet_id, 'reason': reason, 'file': row['file']})
            continue

        units = payload.get('units', [])
        total_units += len(units)
        packet_conf = payload.get('overall_confidence')
        packet_conf_counter[packet_conf] += 1

        units_with_evidence = 0
        for u in units:
            change_kind = u.get('change_kind', 'unclear')
            surface_kind = u.get('surface_kind', 'other')
            risk = u.get('risk', 'low')
            conf = u.get('confidence', 'low')
            evidence_paths = u.get('evidence_paths') or []

            kind_counter[change_kind] += 1
            surface_counter[surface_kind] += 1
            risk_counter[risk] += 1
            conf_counter[conf] += 1

            if change_kind == 'unclear':
                unclear_units += 1
            if conf == 'low':
                low_conf_units += 1
            if evidence_paths:
                units_with_evidence += 1
            else:
                units_without_evidence += 1

        packet_rows.append(
            {
                'packet_id': packet_id,
                'ecosystem': eco,
                'unit_count': len(units),
                'overall_confidence': packet_conf,
                'units_with_evidence': units_with_evidence,
                'result_file': row['file'],
            }
        )

        reviewed.append(packet_id)
        per_eco[eco]['reviewed'] += 1

    unexpected = sorted(set(by_packet.keys()) - expected_ids)

    coverage = round(len(reviewed) / max(1, len(expected_packets)), 4)
    abstention_rate = round((unclear_units + low_conf_units) / max(1, total_units), 4)

    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source': {
            'packet_index': resolve_analysis_path(packet_index_file),
            'results_dir': resolved_results_dir,
        },
        'summary': {
            'expected_packet_count': len(expected_packets),
            'reviewed_packet_count': len(reviewed),
            'missing_packet_count': len(missing),
            'invalid_packet_count': len(invalid_packets),
            'unexpected_result_count': len(unexpected),
            'coverage_pct': coverage,
            'total_units': total_units,
            'unclear_units': unclear_units,
            'low_confidence_units': low_conf_units,
            'units_without_evidence': units_without_evidence,
            'abstention_rate': abstention_rate,
        },
        'distributions': {
            'packet_overall_confidence': dict(sorted(packet_conf_counter.items())),
            'unit_change_kind': dict(sorted(kind_counter.items())),
            'unit_surface_kind': dict(sorted(surface_counter.items())),
            'unit_risk': dict(sorted(risk_counter.items())),
            'unit_confidence': dict(sorted(conf_counter.items())),
        },
        'coverage_by_ecosystem': {k: v for k, v in sorted(per_eco.items())},
        'packets': sorted(packet_rows, key=lambda r: r['packet_id']),
        'missing_packets': sorted(missing),
        'invalid_packets': sorted(invalid_packets, key=lambda r: r['packet_id']),
        'invalid_result_files': invalid_files,
        'unexpected_results': unexpected,
    }
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload
