
# Create your models here.
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    username = None  # remove username field
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # no username required

    def __str__(self):
        return self.email
