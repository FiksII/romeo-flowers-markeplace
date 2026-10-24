from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from market.referrals import remember


@require_GET
def follow(request, code):
    remember(request, code)
    return redirect("market:home")
