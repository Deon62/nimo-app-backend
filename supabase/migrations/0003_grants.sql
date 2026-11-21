-- Newer Supabase projects don't expose new tables to the Data API automatically,
-- so grant access explicitly. RLS policies still decide which rows a user sees.

grant usage on schema public to anon, authenticated, service_role;

grant all on public.profiles, public.subscriptions, public.payments, public.calls,
  public.call_messages, public.payment_methods to service_role;

grant select, update on public.profiles to authenticated;
grant select on public.subscriptions, public.payments, public.calls, public.call_messages
  to authenticated;

grant usage, select on all sequences in schema public to service_role;
