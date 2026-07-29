"""#71019: doctor must not skip model/provider validation on scalar model: key."""

import inspect


def test_doctor_normalizes_scalar_model_key():
    """Verify that doctor.py calls _normalize_root_model_keys before reading
    model_section, so a scalar `model: openai-codex/gpt-5.6-sol` is converted
    to nested form and the validation block doesn't silently skip."""
    from hermes_cli import doctor
    src = inspect.getsource(doctor)
    assert "_normalize_root_model_keys" in src, (
        "doctor.py must call _normalize_root_model_keys before reading "
        "model_section to handle scalar model: keys (#71019)"
    )