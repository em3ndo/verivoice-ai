"""International phone formatting; this does not verify number ownership."""
import re
import phonenumbers
from .providers.enrollment_api import EnrollmentError


def calling_regions():
    return [{"region": region, "code": "+" + str(phonenumbers.country_code_for_region(region))}
            for region in sorted(phonenumbers.SUPPORTED_REGIONS)]


def normalize_phone(region, number):
    if region not in phonenumbers.SUPPORTED_REGIONS:
        raise EnrollmentError("Select a country calling code.")
    if not number or len(number) > 40 or not re.fullmatch(r"[0-9 ()\-.]+", number):
        raise EnrollmentError("Enter your national phone number without the country code or extension.")
    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        raise EnrollmentError("Enter a valid phone number for the selected country.") from None
    if (parsed.country_code != phonenumbers.country_code_for_region(region)
            or not phonenumbers.is_valid_number_for_region(parsed, region)):
        raise EnrollmentError("Enter a valid phone number for the selected country.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
