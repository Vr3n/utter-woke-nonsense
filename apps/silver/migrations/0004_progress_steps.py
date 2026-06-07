from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('silver', '0003_add_new_columns'),
    ]

    operations = [
        migrations.AlterField(
            model_name='silveringestion',
            name='parse_progress_total',
            field=models.PositiveSmallIntegerField(default=5),
        ),
    ]
