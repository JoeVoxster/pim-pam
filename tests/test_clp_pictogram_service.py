from app.services.clp_pictogram_service import pictogram_review_payload, resolve_clp_pictograms, resolved_pictogram_codes


def test_skin_irritation_and_eye_damage_resolves_only_ghs05() -> None:
    decisions = resolve_clp_pictograms(["Skin Irrit. 2; H315", "Eye Dam. 1; H318"], ["GHS05", "GHS07"])

    assert resolved_pictogram_codes(decisions) == ["GHS05"]
    ghs07 = next(decision for decision in decisions if decision.code == "GHS07")
    assert ghs07.suppressed_by_priority is True
    assert ghs07.required is False


def test_skin_irritation_alone_resolves_ghs07() -> None:
    decisions = resolve_clp_pictograms(["Skin Irrit. 2; H315"], ["GHS07"])

    assert resolved_pictogram_codes(decisions) == ["GHS07"]


def test_eye_damage_and_stot_se3_keeps_ghs05_and_ghs07() -> None:
    decisions = resolve_clp_pictograms(["Eye Dam. 1; H318", "STOT SE 3; H335"], ["GHS05", "GHS07"])

    assert resolved_pictogram_codes(decisions) == ["GHS05", "GHS07"]
    ghs07 = next(decision for decision in decisions if decision.code == "GHS07")
    assert ghs07.suppressed_by_priority is False
    assert "STOT SE 3" in ghs07.reason_classifications


def test_review_payload_contains_original_resolved_and_suppressed_codes() -> None:
    payload = pictogram_review_payload("Piktogramme: GHS05, GHS07\nSkin Irrit. 2, Eye Dam. 1\nH315\nH318")

    assert payload["original_label_pictograms"] == ["GHS05", "GHS07"]
    assert payload["resolved_label_pictograms"] == ["GHS05"]
    assert payload["suppressed_pictograms"][0]["code"] == "GHS07"
    assert payload["auto_apply"] is True
