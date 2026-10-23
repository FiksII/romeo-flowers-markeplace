"""Prepare the catalogue previews and private image cache before release cutover."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.signing import Signer

from market.models import Listing
from market.photo_delivery import cached_photo
from market.templatetags.market_tags import listing_gallery
from market.thumbnails import listing_photo


class Command(BaseCommand):
    help = "Generate bouquet previews and warm the same-origin S3 photo cache."

    def handle(self, *args, **options):
        if not settings.S3_ENABLED:
            self.stdout.write("S3 is disabled; no photo proxy cache is needed.")
            return
        urls = set()

        def add_preview(preview):
            urls.add(preview["src"])
            urls.update(
                candidate.split()[0]
                for candidate in preview.get("srcset", "").split(", ")
                if candidate
            )

        listings = (
            Listing.objects.filter(shop__status="active", product__is_public=True)
            .select_related("product")
            .prefetch_related("product__images")
        )
        for listing in listings.iterator(chunk_size=100):
            if not listing.photo:
                continue
            add_preview(listing_photo(listing, "card"))
            for image in listing_gallery(listing):
                urls.add(image["original"])
                add_preview(image["detail"])
                if "thumb" in image:
                    add_preview(image["thumb"])
        names = {
            Signer(salt="market.photo").unsign_object(url.split("/")[-2])
            for url in urls
            if url.startswith("/photos/")
        }
        try:
            for name in sorted(names):
                cached_photo(name)
        except Exception as exc:
            raise CommandError(
                "Could not warm the photo cache; release must not switch."
            ) from exc
        self.stdout.write(self.style.SUCCESS(f"PHOTO_CACHE_WARMED {len(names)}"))
