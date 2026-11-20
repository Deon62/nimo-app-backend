-- Saved payment methods, plus storage for avatars and call recordings.

create table if not exists public.payment_methods (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null check (kind in ('card', 'mpesa')),
  -- card
  brand text,
  last4 text,
  exp_month text,
  exp_year text,
  email text,
  -- Paystack reusable authorization. Server-only: there is no select policy for clients.
  authorization_code text,
  signature text,
  -- mpesa
  phone text,
  is_default boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists payment_methods_user_idx on public.payment_methods (user_id, created_at);
create unique index if not exists payment_methods_card_unique on public.payment_methods (user_id, signature)
  where kind = 'card' and signature is not null;
create unique index if not exists payment_methods_mpesa_unique on public.payment_methods (user_id, phone)
  where kind = 'mpesa';
alter table public.payment_methods enable row level security;
-- No client policies: the app reads payment methods through the API, which hides authorization codes.

-- Storage buckets -------------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true), ('recordings', 'recordings', false)
on conflict (id) do nothing;

-- Avatars: anyone can view (public bucket); users manage files in a folder named after their id.
create policy "avatars: users upload own" on storage.objects for insert to authenticated
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "avatars: users update own" on storage.objects for update to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "avatars: users delete own" on storage.objects for delete to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
-- Recordings: no client policies; the API serves them through short-lived signed URLs.

-- Keep payment records (tax/accounting) when a user deletes their account, without the user link.
alter table public.payments alter column user_id drop not null;
alter table public.payments drop constraint if exists payments_user_id_fkey;
alter table public.payments
  add constraint payments_user_id_fkey foreign key (user_id) references auth.users (id) on delete set null;
