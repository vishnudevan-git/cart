from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()


class CaseInsensitiveBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # Accept username, email, or phone
        user = None
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            pass

        if user is None and username:
            try:
                user = User.objects.get(email__iexact=username)
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                user = User.objects.filter(email__iexact=username).first()

        if user is None and username:
            try:
                user = User.objects.get(phone=username)
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                user = User.objects.filter(phone=username).first()

        if user and user.is_active and not user.is_deleted and user.check_password(password):
            # Rule 1 (kb/08): a suspended partner (supplier / stockist /
            # distributor / marketplace) cannot authenticate.
            from suppliers.suspension import is_user_suspended_partner
            if is_user_suspended_partner(user):
                return None
            return user
        return None
