-- Nimo schema. Run in the Supabase SQL editor or with `supabase db push`.
-- The API uses the service-role key (bypasses RLS); the policies below let the app read its own rows directly.

create extension if not exists pgcrypto;

-- Profiles ---------------------------------------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  first_name text,
  last_name text,
  phone text,
  avatar_url text,
  created_at timestamptz not null default now()
);

-- Create a profile whenever someone signs up (email, Google, Apple or phone).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, first_name, last_name, phone)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'first_name', split_part(new.raw_user_meta_data ->> 'full_name', ' ', 1)),
    new.raw_user_meta_data ->> 'last_name',
    new.phone
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Subscriptions ----------------------------------------------------------------------------------
create table if not exists public.subscriptions (
  user_id uuid primary key references auth.users (id) on delete cascade,
  plan_id text not null check (plan_id in ('free', 'plus', 'pro')),
  period text not null check (period in ('monthly', 'yearly')),
  status text not null default 'active' check (status in ('active', 'cancelled', 'expired')),
  current_period_end timestamptz,
  updated_at timestamptz not null default now()
);

-- Payments ---------------------------------------------------------------------------------------
create table if not exists public.payments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  provider text not null check (provider in ('paystack', 'mpesa')),
  plan_id text not null check (plan_id in ('plus', 'pro')),
  period text not null check (period in ('monthly', 'yearly')),
  amount_minor integer not null check (amount_minor > 0),
  currency text not null,
  -- Paystack reference, or Daraja MerchantRequestID
  reference text not null unique,
  -- Daraja CheckoutRequestID
  checkout_request_id text unique,
  phone text,
  status text not null default 'pending' check (status in ('pending', 'paid', 'failed')),
  receipt text,
  failure_reason text,
  activated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists payments_user_idx on public.payments (user_id, created_at desc);

-- Calls ------------------------------------------------------------------------------------------
create table if not exists public.calls (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  agent_id text not null,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_seconds integer not null default 0,
  recorded boolean not null default false
);
create index if not exists calls_user_idx on public.calls (user_id, started_at desc);

create table if not exists public.call_messages (
  id bigint generated always as identity primary key,
  call_id uuid not null references public.calls (id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz not null default now()
);
create index if not exists call_messages_call_idx on public.call_messages (call_id, id);

-- Row level security -----------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.subscriptions enable row level security;
alter table public.payments enable row level security;
alter table public.calls enable row level security;
alter table public.call_messages enable row level security;

create policy "own profile: read" on public.profiles for select using ((select auth.uid()) = id);
create policy "own profile: update" on public.profiles for update using ((select auth.uid()) = id);
create policy "own subscription: read" on public.subscriptions for select using ((select auth.uid()) = user_id);
create policy "own payments: read" on public.payments for select using ((select auth.uid()) = user_id);
create policy "own calls: read" on public.calls for select using ((select auth.uid()) = user_id);
create policy "own call messages: read" on public.call_messages for select
  using (exists (select 1 from public.calls c where c.id = call_id and c.user_id = (select auth.uid())));
