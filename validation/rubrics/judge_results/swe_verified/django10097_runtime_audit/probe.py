import json
from django.conf import settings
settings.configure(USE_I18N=False)
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
url='http://foo/bar@example.com'
try:
    URLValidator()(url)
    rejected=False
except ValidationError:
    rejected=True
print(json.dumps({'url':url,'should_be_rejected':True,'actually_rejected':rejected}))
