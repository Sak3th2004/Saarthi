"""Delivery targets must be linked to the person named in the reviewed message."""
from __future__ import annotations

import re
import time

from saarthi_mcp.models import Person, Role


def scoped_family_contacts(directory: dict, person_id: str) -> list[Person]:
    linked = set()
    for edge in directory['relationships']:
        if not edge['relation'].strip():
            continue
        if edge['from_person'] == person_id:
            linked.add(edge['to_person'])
        if edge['to_person'] == person_id:
            linked.add(edge['from_person'])
    contacts = []
    for person in directory['people']:
        if person['id'] not in linked or person['id'] == person_id or person['role'] != Role.family.value:
            continue
        email = (person.get('email') or '').strip()
        if not email:
            continue
        if len(email) > 200 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            raise ValueError('A linked family email is invalid. Correct it before sending.')
        contacts.append(Person(id=person['id'], name=person['name'], role=Role.family, email=email))
    return contacts


def recipient_emails(directory: dict, person_id: str) -> list[str]:
    emails = sorted({p.email for p in scoped_family_contacts(directory, person_id)})
    if len(emails) > 50:
        raise ValueError('At most 50 linked family email addresses can receive one message.')
    return emails


def candidate_channels(directory: dict, person_id: str) -> list[str]:
    return [f'email:{email}' for email in recipient_emails(directory, person_id)]


def validate_notification_claim(operation: dict | None, actor: str, directory: dict) -> bool:
    """Run while holding the same repository lock used by directory edits."""
    if operation is None or operation['actor'] != actor:
        raise ValueError('This message review is unavailable to the signed-in caregiver.')
    if operation['state'] != 'pending':
        return False
    if operation['expires'] <= time.time():
        raise ValueError('Review expired. Review the family message again before sending.')
    if recipient_emails(directory, operation['person']['id']) != operation['recipient_emails']:
        raise ValueError('Family recipients changed. Review the message again before sending.')
    return True
