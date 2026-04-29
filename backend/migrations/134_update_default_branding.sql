-- Replace legacy branding defaults without overwriting user-customized values.
UPDATE settings
SET value = 'AI Gateway',
    updated_at = NOW()
WHERE key = 'site_name'
  AND value = 'Sub2API';

UPDATE settings
SET value = 'AI API Gateway Platform',
    updated_at = NOW()
WHERE key = 'site_subtitle'
  AND value = 'Subscription to API Conversion Platform';
