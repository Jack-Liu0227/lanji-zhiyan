-- 每步「确认交付」时把这一步的产物记在台账上（JSON）：
--   {"at":"…","summary":"一句话","items":{"入库论文":"12 篇",…},"files":["…"],"turns":3}
-- 由 workflow.step_delivery() 现算 —— 从库和文件里读，不采信模型自报。
-- 用途：下一步的「进度同步」/交接、以及事后回答「这一步到底交付了什么」。
ALTER TABLE steps ADD COLUMN delivery TEXT NOT NULL DEFAULT '';
