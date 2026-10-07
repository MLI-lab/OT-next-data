import pytest
from solution import *

def test_create_cloned_field_clones_nested_fields_correctly():
    # Arrange
    nested_field = ModelField(name='nested_field', type_=ModelField('Nested', {}, required=True))
    original_field = ModelField(name='original_field', type_=ModelField('Original', {}, required=True))
    original_field.__fields__['nested_field'] = nested_field
    
    # Act
    cloned_field = create_cloned_field(original_field)
    
    # Assert
    assert cloned_field.__fields__['nested_field'] is not nested_field, \
        "Cloned field should not reference the original nested field."
    assert cloned_field.__fields__['nested_field'].name == 'nested_field', \
        "Cloned nested field name should remain the same."
    
def test_create_cloned_field_keeps_field_metadata():
    # Arrange
    original_field = ModelField(name='original_field', type_=ModelField('Original', {}, required=True, default='default_value'))
    
    # Act
    cloned_field = create_cloned_field(original_field)
    
    # Assert
    assert cloned_field.name == 'original_field', "Cloned field name should match the original."
    assert cloned_field.type_ == original_field.type_, "Cloned field type should match the original."
    assert cloned_field.required == original_field.required, "Cloned field required status should match the original."
    assert cloned_field.default == original_field.default, "Cloned field default value should match the original."

def test_create_cloned_field_preserves_validators():
    # Arrange
    original_field = ModelField(name='validated_field', type_=ModelField('Validated', {}, required=True))
    original_field.__validators__['validator_1'] = lambda x: x  # Dummy validator
    
    # Act
    cloned_field = create_cloned_field(original_field)
    
    # Assert
    assert 'validator_1' in cloned_field.__validators__, \
        "Cloned field should preserve validators from the original field."
    assert cloned_field.__validators__['validator_1'] == original_field.__validators__['validator_1'], \
        "Cloned field should have the same validator as the original."

def test_create_cloned_field_does_not_reference_original():
    # Arrange
    original_field = ModelField(name='field', type_=ModelField('Base', {}, required=False))
    
    # Act
    cloned_field = create_cloned_field(original_field)
    
    # Assert
    assert cloned_field is not original_field, \
        "Cloned field should not be the same instance as the original field."