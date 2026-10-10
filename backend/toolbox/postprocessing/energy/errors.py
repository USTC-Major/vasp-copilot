"""Energy failures carrying stable card/sample/field locations."""
from ...contracts import ToolboxError


class EnergyError(ToolboxError):
    def __init__(self, code, message, status=400, *, card_id=None, sample_id=None, field='card'):
        super().__init__(code, message, status)
        self.field_errors = [{'card_id': card_id, 'sample_id': sample_id, 'field': field,
                              'code': code, 'message': message}]

    def payload(self):
        return {**super().payload(), 'field_errors': self.field_errors}


def located(exc, card_id=None, sample_id=None, field='card'):
    if isinstance(exc, EnergyError):
        for error in exc.field_errors:
            if card_id and not error['card_id']:
                error['card_id'] = card_id
            if sample_id and not error['sample_id']:
                error['sample_id'] = sample_id
            if error['field'] == 'card' and field != 'card':
                error['field'] = field
        return exc
    return EnergyError(exc.code, str(exc), exc.status, card_id=card_id, sample_id=sample_id, field=field)
