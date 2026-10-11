class ModelField:
    def __init__(self, name, type_, required=False, default=None):
        self.name = name
        self.type_ = type_
        self.required = required
        self.default = default
        self.__fields__ = {}
        self.__validators__ = {}

def create_cloned_field(field: ModelField) -> ModelField:
    """
    Creates a cloned version of a given ModelField.
    This is a buggy implementation that does not clone nested fields correctly.
    """
    use_type = ModelField(
        name=field.name,
        type_=field.type_,
        required=field.required,
        default=field.default,
    )
    original_type = field.type_

    for f in original_type.__fields__.values():
        use_type.__fields__[f.name] = f  # This is the buggy line; it should call create_cloned_field(f)

    use_type.__validators__ = original_type.__validators__
    
    return use_type

__all__ = ['ModelField', 'create_cloned_field']