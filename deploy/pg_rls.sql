-- PostgreSQL RLS 双保险（M1 启用；M0 为应用层 tenant 过滤）
-- 用法: ALTER TABLE ... ENABLE ROW LEVEL SECURITY; 连接级 SET app.tenant_id = '...'
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['res_partner','product_sku','price_history','sales_order',
    'sales_order_line','stock_quant','stock_move','journal_entry','journal_line',
    'tax_invoice','finance_payment','outbox','approval','audit_log','agent_action_log',
    'invariant_check']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format($f$CREATE POLICY tenant_isolation ON %I
      USING (tenant_id = current_setting('app.tenant_id'))$f$, t);
  END LOOP;
END $$;
