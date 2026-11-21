-- Give an account the Pro plan without paying (admin / testing).
-- Run in the Supabase SQL editor. Set ONE of the two values below, leave the other as ''.
-- Phone numbers are stored without the '+', e.g. '254712345678'.

with target as (
  select id from auth.users
  where (email = 'you@example.com' and email <> '')
     or (phone = '' and phone <> '')
  limit 1
)
insert into public.subscriptions (user_id, plan_id, period, status, current_period_end, updated_at)
select id, 'pro', 'yearly', 'active', now() + interval '10 years', now() from target
on conflict (user_id) do update
  set plan_id = 'pro',
      period = 'yearly',
      status = 'active',
      current_period_end = now() + interval '10 years',
      updated_at = now()
returning user_id, plan_id, status, current_period_end;

-- No row returned? The email/phone didn't match. Find your account with:
--   select id, email, phone, created_at from auth.users order by created_at desc limit 20;
--
-- Back to free:
--   update public.subscriptions set plan_id = 'free', status = 'expired' where user_id = '<id>';
