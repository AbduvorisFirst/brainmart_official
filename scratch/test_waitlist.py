import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.settings")
django.setup()

from unittest.mock import patch
from core.models import Store, Customer, Product, ProductVariant, ProductWaitlist
from core.working_views.services import (
    handle_incoming_telegram_message,
    check_and_notify_waitlist_for_new_product,
)

print("--- 1. PREPARING TEST DATA ---")
store = Store.objects.filter(is_active=True).first()
if not store:
    print("ERROR: No active store found in DB")
    exit(1)

print(f"Testing with store: {store.name} (ID: {store.id})")

# Clear any previous test waitlist entries and products for clean test
ProductWaitlist.objects.filter(store=store, query_text__icontains="etik").delete()
Product.objects.filter(store=store, name__icontains="etik").delete()

test_chat_id = "998901234567_test"
customer, _ = Customer.objects.get_or_create(
    telegram_chat_id=test_chat_id,
    defaults={
        "instagram_scoped_id": f"tg_{test_chat_id}",
        "full_name": "Alisher Qodirov",
        "language": "uz",
    }
)
customer.language = "uz"
customer.save()

print(f"Test customer created: {customer.full_name} (chat_id: {customer.telegram_chat_id})")

print("\n--- 2. SIMULATING CUSTOMER TELEGRAM INQUIRY FOR OUT-OF-STOCK ITEM ---")
user_query = "etik neche ming va razmerla bormi"
print(f"Customer writes: '{user_query}'")

ai_reply, matches = handle_incoming_telegram_message(
    chat_id=test_chat_id,
    message_text=user_query,
    first_name="Alisher",
    username="alisher_test",
)

print(f"\nAI Response:\n{ai_reply}")
print(f"Matches in stock count: {len(matches)}")

print("\n--- 3. VERIFYING PRODUCT WAITLIST RECORD IN DATABASE ---")
waitlist_entry = ProductWaitlist.objects.filter(
    store=store,
    telegram_chat_id=test_chat_id,
    status=ProductWaitlist.Status.PENDING,
).first()

if not waitlist_entry:
    print("FAILED: No pending waitlist entry found!")
    exit(1)

print(f"SUCCESS: Waitlist entry #{waitlist_entry.id} found!")
print(f"  - Store: {waitlist_entry.store.name}")
print(f"  - Customer: {waitlist_entry.customer.full_name}")
print(f"  - Query text: {waitlist_entry.query_text}")
print(f"  - Status: {waitlist_entry.status}")
print(f"  - Language: {waitlist_entry.language}")

print("\n--- 4. SELLER ADDS PRODUCT 'Klassik qora charm etik' ---")
# Create test product that matches customer's waitlist query
test_product = Product.objects.create(
    store=store,
    name="Klassik qora charm etik",
    description="Haqiqiy charm, qishki issiq astarli erkaklar etigi",
    base_price=520000.00,
    is_active=True,
)
ProductVariant.objects.create(
    product=test_product,
    color="Qora",
    size="42",
    stock_quantity=10,
    sku=f"TEST-ETIK-42-{test_product.id}",
)
print(f"Product #{test_product.id} '{test_product.name}' created with 10 units stock.")

print("\n--- 5. TRIGGERING AUTOMATIC WAITLIST NOTIFICATION DISPATCH ---")
with patch("core.working_views.telegram_bot._send_telegram_photo", return_value=True) as mock_photo, \
     patch("core.working_views.telegram_bot._send_telegram_message", return_value=True) as mock_msg:
    notified_count = check_and_notify_waitlist_for_new_product(test_product)

print(f"Notified count returned: {notified_count}")

# Verify DB state of waitlist entry
waitlist_entry.refresh_from_db()
print("\n--- 6. VERIFYING WAITLIST ENTRY UPDATED TO NOTIFIED ---")
print(f"  - Status: {waitlist_entry.status} (expected: notified)")
print(f"  - Notified Product: {waitlist_entry.notified_product}")
print(f"  - Notified At: {waitlist_entry.notified_at}")

assert waitlist_entry.status == ProductWaitlist.Status.NOTIFIED, "Status must be 'notified'"
assert waitlist_entry.notified_product == test_product, "Notified product must match test_product"
assert waitlist_entry.notified_at is not None, "Notified at timestamp must be set"
assert notified_count >= 1, "At least 1 customer must be notified"
assert mock_photo.called or mock_msg.called, "Telegram notification must have been dispatched"

print("\n--- 7. TESTING DASHBOARD VIEW seller_waitlist_view ---")
from django.test import RequestFactory
from core.working_views.views_dashboard import seller_waitlist_view

rf = RequestFactory()
request = rf.get("/dashboard/waitlist/")
request.user = store.owner

response = seller_waitlist_view(request)
print(f"seller_waitlist_view status code: {response.status_code}")
assert response.status_code == 200, "Dashboard waitlist view must return 200 OK"
assert b"Waitlist" in response.content or b"\xd0\x9b\xd0\xb8\xd1\x81\xd1\x82 \xd0\xbe\xd0\xb6\xd0\xb8\xd0\xb4\xd0\xb0\xd0\xbd\xd0\xb8\xd1\x8f" in response.content

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print("\n==========================================")
print("SUCCESS: ALL WAITLIST LIFECYCLE TESTS PASSED 100%!")
print("==========================================")

# Clean up test product and test waitlist
test_product.delete()
waitlist_entry.delete()

