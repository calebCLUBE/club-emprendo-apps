# applications/models.py (or wherever your models live)
from django.db import models

class FormGroup(models.Model):
    number = models.PositiveIntegerField(unique=True)   # e.g. 6
    start_month = models.CharField(max_length=30)       # e.g. "Enero"
    end_month = models.CharField(max_length=30)         # e.g. "Marzo"
    year = models.PositiveIntegerField()                # e.g. 2026
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Grupo {self.number} ({self.start_month}–{self.end_month} {self.year})"
