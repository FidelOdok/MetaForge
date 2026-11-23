"""Which cad_models are the product's own geometry."""

from twin_core.model_roles import is_product_geometry, role_of


def test_design_is_the_default_and_the_only_product_role() -> None:
    assert role_of({}) == "design"
    assert role_of(None) == "design"
    assert is_product_geometry({})
    assert is_product_geometry({"model_role": "design"})


def test_analysis_and_component_models_are_not_the_product() -> None:
    assert not is_product_geometry({"model_role": "analysis"})
    assert not is_product_geometry({"model_role": "component"})
