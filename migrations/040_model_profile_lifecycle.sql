ALTER TABLE model_profiles ADD COLUMN retired INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_profiles ADD COLUMN runtime_revision INTEGER NOT NULL DEFAULT 1;

CREATE TRIGGER model_profile_runtime_revision
AFTER UPDATE OF base_url, api_key, model, retired ON model_profiles
WHEN NEW.base_url IS NOT OLD.base_url OR NEW.api_key IS NOT OLD.api_key
  OR NEW.model IS NOT OLD.model OR NEW.retired IS NOT OLD.retired
BEGIN
    UPDATE model_profiles SET runtime_revision = OLD.runtime_revision + 1 WHERE id = NEW.id;
END;
