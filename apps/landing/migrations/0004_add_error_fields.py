from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0003_add_progress_and_entry_task_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='landingupload',
            name='error_type',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='landingupload',
            name='error_message',
            field=models.TextField(blank=True, default=''),
        ),
    ]
