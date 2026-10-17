from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm


class ClassLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Roll Number / University Email"
        self.fields["username"].widget.attrs.update({
            "placeholder": "e.g. BITF24A001 or admin@pucit.edu.pk",
            "class": "form-control",
            "autofocus": True,
        })
        self.fields["password"].widget.attrs.update({
            "class": "form-control",
        })

    def clean_username(self):
        username = self.cleaned_data.get("username", "")
        return username.strip()


class ForgotPasswordRequestForm(forms.Form):
    identifier = forms.CharField(
        label="Roll Number or University Email",
        max_length=150,
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. BITF24A001 or admin@pucit.edu.pk",
            "class": "form-control",
            "autofocus": True,
        }),
        help_text="Students: Enter your Roll Number. Admins/CRs: Enter your registered Email.",
    )

    def clean_identifier(self):
        ident = self.cleaned_data.get("identifier", "").strip()
        if not ident:
            raise forms.ValidationError("Please enter your roll number or university email.")
        return ident


class VerifyResetCodeForm(forms.Form):
    code = forms.CharField(
        label="6-Digit Verification Code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            "placeholder": "Enter 6-digit code",
            "class": "form-control",
            "pattern": "[0-9]{6}",
            "inputmode": "numeric",
            "autocomplete": "one-time-code",
            "autofocus": True,
        }),
    )

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip()
        if not code.isdigit() or len(code) != 6:
            raise forms.ValidationError("Please enter a valid 6-digit numeric verification code.")
        return code


class ResetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

