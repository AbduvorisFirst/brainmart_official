# Generated manually for backfilling store slugs
from django.db import migrations
import re

def populate_slugs(apps, schema_editor):
    Store = apps.get_model('core', 'Store')
    for store in Store.objects.all():
        if not store.slug:
            base = (store.instagram_username or store.name or f"store-{store.pk}").strip().lower()
            translit_map = {
                'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
                'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
                'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
                'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
                'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
                'ў': 'o', 'ғ': 'g', 'қ': 'q', 'ҳ': 'h'
            }
            for ru, lat in translit_map.items():
                base = base.replace(ru, lat)
            cleaned = re.sub(r'[^a-z0-9]+', '-', base).strip('-')
            if not cleaned:
                cleaned = f"store-{store.pk}"

            cand = cleaned
            counter = 1
            while Store.objects.filter(slug=cand).exclude(pk=store.pk).exists():
                cand = f"{cleaned}-{counter}"
                counter += 1
            store.slug = cand
            store.save(update_fields=['slug'])

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0018_store_subdomain_and_bot'),
    ]

    operations = [
        migrations.RunPython(populate_slugs, reverse_code=migrations.RunPython.noop),
    ]
