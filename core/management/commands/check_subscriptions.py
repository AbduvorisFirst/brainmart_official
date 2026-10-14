from django.core.management.base import BaseCommand
from core.working_views.subscription_service import check_and_notify_all_subscriptions

class Command(BaseCommand):
    help = "Проверяет сроки подписок всех магазинов, рассылает предупреждения в Telegram и блокирует просроченные магазины (после 5 дней)"

    def handle(self, *args, **options):
        self.stdout.write("Проверка подписок магазинов brainmart...")
        results = check_and_notify_all_subscriptions()
        self.stdout.write(
            self.style.SUCCESS(
                f"Успешно: проверено магазинов: {results['checked']}, "
                f"отправлено уведомлений: {results['notified']}, "
                f"заблокировано: {results['suspended']}"
            )
        )
