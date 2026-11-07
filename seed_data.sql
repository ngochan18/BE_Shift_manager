DO $$
DECLARE
    -- Store ID and Coordinates
    v_store1_id integer;
    v_store1_lat numeric;
    v_store1_lng numeric;

    -- Shift Templates IDs
    v_temp11_id integer; -- Ca Sáng
    v_temp12_id integer; -- Ca Chiều
    v_temp13_id integer; -- Ca Tối

    -- Employee IDs
    v_emp1_id integer;
    v_emp2_id integer;
    v_emp3_id integer;
    v_emp4_id integer;
    v_emp5_id integer;
    v_emp6_id integer;
    v_emp7_id integer;
    v_admin_id integer;

    -- Employee hourly rates
    v_rate1 numeric;
    v_rate2 numeric;
    v_rate3 numeric;
    v_rate4 numeric;
    v_rate5 numeric;
    v_rate6 numeric;
    v_rate7 numeric;

    -- Loop variables
    v_date date;
    v_shift_id integer;
    v_multiplier numeric;

    -- Payroll Period IDs
    v_period1_id integer;
    v_period2_id integer;

    -- Check-in simulation variables
    v_rand numeric;
    v_case_rand numeric;
    v_status varchar;
    v_checkin timestamptz;
    v_checkout timestamptz;
    v_lat numeric;
    v_lng numeric;
    v_dist integer;
    v_geo_valid boolean;
    v_late_min integer;
    v_early_min integer;
    v_worked_min integer;
    v_auto_checkout boolean;
    v_is_manual boolean;
    v_override_reason varchar;
    
    -- Control variables
    v_temp_id integer;
    v_start_time time;
    v_end_time time;
    v_emp_id integer;
    v_emp_idx integer;
    v_num_workers integer;
    v_is_emergency boolean;
    v_is_open_shift boolean;
    i integer;
BEGIN
    RAISE NOTICE '=== BẮT ĐẦU TẠO DỮ LIỆU ĐA DẠNG CHO 1 CỬA HÀNG HIỆN CÓ ===';

    -- 1. Tìm tài khoản admin/manager làm người tạo ca
    SELECT id INTO v_admin_id FROM users WHERE is_superuser = true OR role = 'admin' LIMIT 1;
    IF v_admin_id IS NULL THEN
        SELECT id INTO v_admin_id FROM users LIMIT 1;
    END IF;

    -- 2. Sử dụng cửa hàng hiện có duy nhất trong cơ sở dữ liệu của bạn
    SELECT id, geo_latitude, geo_longitude INTO v_store1_id, v_store1_lat, v_store1_lng FROM stores ORDER BY id LIMIT 1;
    IF v_store1_id IS NULL THEN
        RAISE EXCEPTION 'Lỗi: Cơ sở dữ liệu của bạn chưa có bất kỳ cửa hàng nào có sẵn!';
    END IF;

    -- 3. Tự động lấy 3 tài khoản nhân viên hiện có trong DB của bạn
    SELECT id INTO v_emp1_id FROM users WHERE role = 'employee' ORDER BY id LIMIT 1 OFFSET 0;
    SELECT id INTO v_emp2_id FROM users WHERE role = 'employee' ORDER BY id LIMIT 1 OFFSET 1;
    SELECT id INTO v_emp3_id FROM users WHERE role = 'employee' ORDER BY id LIMIT 1 OFFSET 2;

    -- Kiểm tra nếu không đủ 3 nhân viên sẵn có trong cơ sở dữ liệu
    IF v_emp1_id IS NULL OR v_emp2_id IS NULL OR v_emp3_id IS NULL THEN
        RAISE EXCEPTION 'Lỗi: Cơ sở dữ liệu của bạn cần phải có sẵn ít nhất 3 nhân viên với role = ''employee'' trong bảng users!';
    END IF;

    -- 4. Xóa toàn bộ dữ liệu giao dịch cũ (Tháng 4 và Tháng 5/2026) của cửa hàng này để tránh trùng lặp
    -- Xóa các thông báo liên quan đến yêu cầu đổi ca của các ca này
    DELETE FROM notifications WHERE related_swap_id IN (
        SELECT id FROM shift_swap_requests WHERE target_shift_id IN (
            SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31'
        ) OR offered_shift_id IN (
            SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31'
        )
    );

    -- Xóa các thông báo trực tiếp liên quan đến các ca này
    DELETE FROM notifications WHERE related_shift_id IN (
        SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31'
    );

    -- Xóa các yêu cầu đổi ca liên quan đến các ca này
    DELETE FROM shift_swap_requests WHERE target_shift_id IN (
        SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31'
    ) OR offered_shift_id IN (
        SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31'
    );

    DELETE FROM check_in_records WHERE shift_id IN (SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31');
    DELETE FROM shift_assignments WHERE shift_id IN (SELECT id FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31');
    DELETE FROM shifts WHERE store_id = v_store1_id AND date >= '2026-04-01' AND date <= '2026-05-31';
    DELETE FROM payroll_details WHERE period_id IN (SELECT id FROM payroll_periods WHERE store_id = v_store1_id AND year = 2026 AND month IN (4, 5));
    DELETE FROM payroll_periods WHERE store_id = v_store1_id AND year = 2026 AND month IN (4, 5);

    -- Xóa các thông báo, chấm công, ca làm, bảng lương liên quan trực tiếp đến các nhân viên mẫu (NV4 -> NV7) để tránh lỗi khóa ngoại khi xóa tài khoản
    DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));
    DELETE FROM shift_swap_requests WHERE requester_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'))
                                       OR partner_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));
    DELETE FROM check_in_records WHERE user_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));
    DELETE FROM shift_assignments WHERE user_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));
    DELETE FROM payroll_details WHERE user_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));

    -- Xóa hồ sơ nhân viên và tài khoản của 4 nhân viên mẫu để tạo mới hoàn toàn
    DELETE FROM employee_profiles WHERE user_id IN (SELECT id FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777'));
    DELETE FROM users WHERE phone IN ('0904444444', '0905555555', '0906666666', '0907777777');

    -- Xóa các ca mẫu cũ của cửa hàng này để đảm bảo dữ liệu mới tinh
    DELETE FROM shift_templates WHERE store_id = v_store1_id AND name IN ('Ca Sáng', 'Ca Chiều', 'Ca Tối');

    -- 5. Tạo hoặc lấy lại 4 nhân viên mẫu bổ sung (NV4, NV5, NV6, NV7)
    SELECT id INTO v_emp4_id FROM users WHERE phone = '0904444444' LIMIT 1;
    IF v_emp4_id IS NULL THEN
        INSERT INTO users (phone, full_name, role, password, is_active, is_staff, is_superuser, date_joined, avatar_url)
        VALUES ('0904444444', 'Phạm Minh Đức (Mẫu)', 'employee', 'pbkdf2_sha256$1200000$rSQUzdLxztF4h7zZV1w4Zg$DM9sj8JDO2lWQzRiOceCFLA78T84vRdPXgfW1A987U4=', true, false, false, NOW(), '')
        RETURNING id INTO v_emp4_id;
        
        INSERT INTO employee_profiles (user_id, hourly_rate, bank_account, bank_name, id_card_number, address, emergency_contact, start_date, note)
        VALUES (v_emp4_id, 32000.00, '0904444444', 'MB', '079096004444', 'TP. Hồ Chí Minh', '', '2026-01-01', '');
    END IF;

    SELECT id INTO v_emp5_id FROM users WHERE phone = '0905555555' LIMIT 1;
    IF v_emp5_id IS NULL THEN
        INSERT INTO users (phone, full_name, role, password, is_active, is_staff, is_superuser, date_joined, avatar_url)
        VALUES ('0905555555', 'Nguyễn Hoàng Nam (Mẫu)', 'employee', 'pbkdf2_sha256$1200000$rSQUzdLxztF4h7zZV1w4Zg$DM9sj8JDO2lWQzRiOceCFLA78T84vRdPXgfW1A987U4=', true, false, false, NOW(), '')
        RETURNING id INTO v_emp5_id;
        
        INSERT INTO employee_profiles (user_id, hourly_rate, bank_account, bank_name, id_card_number, address, emergency_contact, start_date, note)
        VALUES (v_emp5_id, 27000.00, '1012345555', 'TCB', '079096005555', 'TP. Hồ Chí Minh', '', '2026-01-01', '');
    END IF;

    SELECT id INTO v_emp6_id FROM users WHERE phone = '0906666666' LIMIT 1;
    IF v_emp6_id IS NULL THEN
        INSERT INTO users (phone, full_name, role, password, is_active, is_staff, is_superuser, date_joined, avatar_url)
        VALUES ('0906666666', 'Đỗ Thu Trang (Mẫu)', 'employee', 'pbkdf2_sha256$1200000$rSQUzdLxztF4h7zZV1w4Zg$DM9sj8JDO2lWQzRiOceCFLA78T84vRdPXgfW1A987U4=', true, false, false, NOW(), '')
        RETURNING id INTO v_emp6_id;
        
        INSERT INTO employee_profiles (user_id, hourly_rate, bank_account, bank_name, id_card_number, address, emergency_contact, start_date, note)
        VALUES (v_emp6_id, 31000.00, '2023456666', 'ACB', '079096006666', 'TP. Hồ Chí Minh', '', '2026-01-01', '');
    END IF;

    SELECT id INTO v_emp7_id FROM users WHERE phone = '0907777777' LIMIT 1;
    IF v_emp7_id IS NULL THEN
        INSERT INTO users (phone, full_name, role, password, is_active, is_staff, is_superuser, date_joined, avatar_url)
        VALUES ('0907777777', 'Vũ Việt Hoàng (Mẫu)', 'employee', 'pbkdf2_sha256$1200000$rSQUzdLxztF4h7zZV1w4Zg$DM9sj8JDO2lWQzRiOceCFLA78T84vRdPXgfW1A987U4=', true, false, false, NOW(), '')
        RETURNING id INTO v_emp7_id;
        
        INSERT INTO employee_profiles (user_id, hourly_rate, bank_account, bank_name, id_card_number, address, emergency_contact, start_date, note)
        VALUES (v_emp7_id, 29000.00, '1903456777', 'VCB', '079096007777', 'TP. Hồ Chí Minh', '', '2026-01-01', '');
    END IF;

    -- Lấy thông tin mức lương của cả 7 nhân viên
    SELECT COALESCE(hourly_rate, 25000.00) INTO v_rate1 FROM employee_profiles WHERE user_id = v_emp1_id;
    SELECT COALESCE(hourly_rate, 30000.00) INTO v_rate2 FROM employee_profiles WHERE user_id = v_emp2_id;
    SELECT COALESCE(hourly_rate, 28000.00) INTO v_rate3 FROM employee_profiles WHERE user_id = v_emp3_id;
    SELECT COALESCE(hourly_rate, 32000.00) INTO v_rate4 FROM employee_profiles WHERE user_id = v_emp4_id;
    SELECT COALESCE(hourly_rate, 27000.00) INTO v_rate5 FROM employee_profiles WHERE user_id = v_emp5_id;
    SELECT COALESCE(hourly_rate, 31000.00) INTO v_rate6 FROM employee_profiles WHERE user_id = v_emp6_id;
    SELECT COALESCE(hourly_rate, 29000.00) INTO v_rate7 FROM employee_profiles WHERE user_id = v_emp7_id;

    -- 6. Tạo các ca mẫu (Shift Templates) cho cửa hàng duy nhất này
    SELECT id INTO v_temp11_id FROM shift_templates WHERE store_id = v_store1_id AND name = 'Ca Sáng';
    IF v_temp11_id IS NULL THEN
        INSERT INTO shift_templates (store_id, name, start_time, end_time, min_workers, max_workers, is_active)
        VALUES (v_store1_id, 'Ca Sáng', '08:00:00', '12:00:00', 1, 2, true) RETURNING id INTO v_temp11_id;
    END IF;
    SELECT id INTO v_temp12_id FROM shift_templates WHERE store_id = v_store1_id AND name = 'Ca Chiều';
    IF v_temp12_id IS NULL THEN
        INSERT INTO shift_templates (store_id, name, start_time, end_time, min_workers, max_workers, is_active)
        VALUES (v_store1_id, 'Ca Chiều', '13:00:00', '17:00:00', 1, 2, true) RETURNING id INTO v_temp12_id;
    END IF;
    SELECT id INTO v_temp13_id FROM shift_templates WHERE store_id = v_store1_id AND name = 'Ca Tối';
    IF v_temp13_id IS NULL THEN
        INSERT INTO shift_templates (store_id, name, start_time, end_time, min_workers, max_workers, is_active)
        VALUES (v_store1_id, 'Ca Tối', '18:00:00', '22:00:00', 1, 2, true) RETURNING id INTO v_temp13_id;
    END IF;

    -- 6. Tạo Ca làm, Phân ca & Chấm công từng ngày từ 01/04/2026 đến 31/05/2026
    v_date := '2026-04-01';
    WHILE v_date <= '2026-05-31' LOOP
        -- Chủ nhật hệ số lương x1.5, ngày thường x1.0
        IF EXTRACT(ISODOW FROM v_date) = 7 THEN
            v_multiplier := 1.50;
        ELSE
            v_multiplier := 1.00;
        END IF;

        -- Lặp qua 3 ca Sáng, Chiều, Tối của cửa hàng
        FOR shift_idx IN 1..3 LOOP
            IF shift_idx = 1 THEN v_temp_id := v_temp11_id; v_start_time := '08:00:00'; v_end_time := '12:00:00';
            ELSIF shift_idx = 2 THEN v_temp_id := v_temp12_id; v_start_time := '13:00:00'; v_end_time := '17:00:00';
            ELSE v_temp_id := v_temp13_id; v_start_time := '18:00:00'; v_end_time := '22:00:00';
            END IF;

            -- Thiết lập thuộc tính ca làm ngẫu nhiên
            v_rand := random();
            v_is_open_shift := false;
            v_is_emergency := false;

            IF v_rand < 0.08 THEN
                v_is_open_shift := true; -- Ca trống đang chờ tranh
            ELSIF v_rand < 0.14 THEN
                v_is_emergency := true;  -- Ca khẩn cấp
            END IF;

            -- Thêm ca làm việc
            INSERT INTO shifts (store_id, template_id, date, start_time, end_time, is_open, is_emergency, max_workers, salary_multiplier, created_by_id, created_at, note)
            VALUES (v_store1_id, v_temp_id, v_date, v_start_time, v_end_time, v_is_open_shift, v_is_emergency, 2, v_multiplier, v_admin_id, NOW(), '')
            RETURNING id INTO v_shift_id;

            -- Nếu không phải ca trống, tiến hành phân bổ nhân viên
            IF NOT v_is_open_shift THEN
                v_num_workers := FLOOR(random() * 2) + 1; -- 1 hoặc 2 nhân viên được phân ca

                FOR worker_idx IN 1..v_num_workers LOOP
                    -- Phân bổ nhân sự xen kẽ xoay ca dựa theo ngày và ca làm việc để tránh trùng lặp
                    v_emp_idx := (EXTRACT(DAY FROM v_date)::integer + shift_idx + worker_idx) % 7;
                    IF v_emp_idx = 0 THEN v_emp_id := v_emp1_id;
                    ELSIF v_emp_idx = 1 THEN v_emp_id := v_emp2_id;
                    ELSIF v_emp_idx = 2 THEN v_emp_id := v_emp3_id;
                    ELSIF v_emp_idx = 3 THEN v_emp_id := v_emp4_id;
                    ELSIF v_emp_idx = 4 THEN v_emp_id := v_emp5_id;
                    ELSIF v_emp_idx = 5 THEN v_emp_id := v_emp6_id;
                    ELSE v_emp_id := v_emp7_id;
                    END IF;

                    -- Thiết lập trạng thái phân ca ngẫu nhiên
                    v_rand := random();
                    IF v_rand < 0.04 THEN
                        v_status := 'pending'; -- Chờ duyệt
                    ELSIF v_rand < 0.08 THEN
                        v_status := 'cancelled'; -- Đã hủy ca
                    ELSE
                        v_status := 'confirmed'; -- Đã duyệt
                    END IF;

                    INSERT INTO shift_assignments (shift_id, user_id, assigned_by_id, assigned_at, status)
                    VALUES (v_shift_id, v_emp_id, v_admin_id, NOW(), v_status);

                    -- Nếu ca đã xác nhận (confirmed) thì sinh lịch sử chấm công Check-in/Check-out
                    IF v_status = 'confirmed' THEN
                        v_case_rand := random();

                        -- Trường hợp bỏ làm (không check-in, không tạo bản ghi chấm công) chiếm khoảng 8%
                        IF v_case_rand >= 0.08 THEN
                            -- Khởi tạo giá trị mặc định cho chấm công đúng giờ
                            v_checkin := (v_date || ' ' || v_start_time || ' +07')::timestamptz;
                            v_checkout := (v_date || ' ' || v_end_time || ' +07')::timestamptz;
                            v_lat := v_store1_lat;
                            v_lng := v_store1_lng;
                            v_dist := FLOOR(random() * 40) + 5; -- 5m đến 45m
                            v_geo_valid := true;
                            v_late_min := 0;
                            v_early_min := 0;
                            v_worked_min := 240; -- Ca 4 tiếng = 240 phút
                            v_auto_checkout := false;
                            v_is_manual := false;
                            v_override_reason := '';

                            -- Phân phối các trường hợp còn lại
                            IF v_case_rand < 0.70 THEN
                                -- Trường hợp 1: Chấm công chuẩn, đúng giờ (khoảng 62%)
                                v_checkin := v_checkin - (FLOOR(random() * 8) * INTERVAL '1 minute'); -- Đến sớm từ 0-7 phút
                                v_checkout := v_checkout + (FLOOR(random() * 8) * INTERVAL '1 minute'); -- Về muộn từ 0-7 phút
                            
                            ELSIF v_case_rand < 0.82 THEN
                                -- Trường hợp 2: Đi muộn (Late check-in) (khoảng 12%)
                                v_late_min := FLOOR(random() * 31) + 10; -- Đi muộn từ 10 đến 40 phút
                                v_checkin := v_checkin + (v_late_min * INTERVAL '1 minute');
                                v_checkout := v_checkout + (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_worked_min := 240 - v_late_min;
                            
                            ELSIF v_case_rand < 0.89 THEN
                                -- Trường hợp 3: Về sớm (Early checkout) (khoảng 7%)
                                v_early_min := FLOOR(random() * 26) + 10; -- Về sớm từ 10 đến 35 phút
                                v_checkin := v_checkin - (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_checkout := v_checkout - (v_early_min * INTERVAL '1 minute');
                                v_worked_min := 240 - v_early_min;
                            
                            ELSIF v_case_rand < 0.93 THEN
                                -- Trường hợp 4: Quên check-out (khoảng 4%)
                                v_checkin := v_checkin - (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_checkout := NULL; -- Không có giờ về
                                v_worked_min := 0;  -- Giờ tính công bằng 0
                                v_auto_checkout := true;
                            
                            ELSIF v_case_rand < 0.96 THEN
                                -- Trường hợp 5: GPS không hợp lệ (Vị trí ngoài bán kính chấm công của cửa hàng) (khoảng 3%)
                                v_checkin := v_checkin - (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_checkout := v_checkout + (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_lat := v_store1_lat + 0.005; -- Lệch vĩ độ
                                v_lng := v_store1_lng + 0.005; -- Lệch kinh độ
                                v_dist := 680; -- Khoảng cách thực tế 680 mét
                                v_geo_valid := false; -- GPS không hợp lệ
                            
                            ELSE
                                -- Trường hợp 6: Check-in được Admin phê duyệt thủ công (khoảng 4%)
                                v_checkin := v_checkin - (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_checkout := v_checkout + (FLOOR(random() * 5) * INTERVAL '1 minute');
                                v_is_manual := true;
                                v_override_reason := 'Nhân viên quên quét mã QR, duyệt công thủ công qua camera đối chiếu';
                            END IF;

                            -- Lưu bản ghi chấm công vào DB
                            INSERT INTO check_in_records (
                                shift_id, user_id, checkin_at, checkout_at, checkin_lat, checkin_lng, 
                                checkin_distance_meters, checkin_method, geo_valid, late_minutes, 
                                early_minutes, worked_minutes, effective_checkin_at, auto_checkout, 
                                is_manual_override, overridden_by_id, override_reason, qr_token_used, note
                            )
                            VALUES (
                                v_shift_id, v_emp_id, v_checkin, v_checkout, v_lat, v_lng, 
                                v_dist, 'qr_gps', v_geo_valid, v_late_min, 
                                v_early_min, v_worked_min, v_checkin, v_auto_checkout, 
                                v_is_manual, CASE WHEN v_is_manual THEN v_admin_id ELSE NULL END, v_override_reason, '', ''
                            );
                        END IF;
                    END IF;
                END LOOP;
            END IF;
        END LOOP;

        v_date := v_date + INTERVAL '1 day';
    END LOOP;

    -- 7. Khởi tạo Bảng chốt lương tháng 4 (đã thanh toán) và tháng 5 (đã chốt) cho cửa hàng
    -- Bảng lương Tháng 4
    INSERT INTO payroll_periods (store_id, month, year, status, created_by_id, confirmed_at, paid_at, note)
    VALUES (v_store1_id, 4, 2026, 'paid', v_admin_id, NOW(), NOW(), 'Bảng lương tháng 4/2026') RETURNING id INTO v_period1_id;
    -- Bảng lương Tháng 5
    INSERT INTO payroll_periods (store_id, month, year, status, created_by_id, confirmed_at, paid_at, note)
    VALUES (v_store1_id, 5, 2026, 'confirmed', v_admin_id, NOW(), null, 'Bảng lương tháng 5/2026') RETURNING id INTO v_period2_id;

    -- 8. Tự động tính toán tổng số giờ làm và lưu thông tin chi tiết bảng lương
    -- Lương tháng 4
    INSERT INTO payroll_details (period_id, user_id, total_shifts, total_hours, hourly_rate, gross_amount, deduction, bonus, net_amount, late_count, early_leave_count, missing_checkout_count, note)
    SELECT 
        v_period1_id, 
        u.id, 
        COUNT(c.id), 
        ROUND(COALESCE(SUM(c.worked_minutes), 0) / 60.0, 2), 
        COALESCE(p.hourly_rate, 25000.00),
        ROUND(COALESCE(SUM(c.worked_minutes / 60.0 * COALESCE(p.hourly_rate, 25000.00) * s.salary_multiplier), 0.00), 2),
        0.00, -- deduction
        0.00, -- bonus
        ROUND(COALESCE(SUM(c.worked_minutes / 60.0 * COALESCE(p.hourly_rate, 25000.00) * s.salary_multiplier), 0.00), 2),
        SUM(CASE WHEN c.late_minutes > 5 THEN 1 ELSE 0 END),
        SUM(CASE WHEN c.checkout_at IS NOT NULL AND c.auto_checkout = false AND c.checkout_at < (s.date || ' ' || s.end_time || ' +07')::timestamptz - INTERVAL '5 minutes' THEN 1 ELSE 0 END),
        SUM(CASE WHEN c.auto_checkout = true OR c.checkout_at IS NULL THEN 1 ELSE 0 END),
        'Dữ liệu điểm danh kết chuyển tự động tháng 4'
    FROM users u
    JOIN employee_profiles p ON u.id = p.user_id
    JOIN check_in_records c ON u.id = c.user_id
    JOIN shifts s ON c.shift_id = s.id
    WHERE s.store_id = v_store1_id AND s.date >= '2026-04-01' AND s.date <= '2026-04-30' AND u.id IN (v_emp1_id, v_emp2_id, v_emp3_id, v_emp4_id, v_emp5_id, v_emp6_id, v_emp7_id)
    GROUP BY u.id, p.hourly_rate;

    -- Lương tháng 5
    INSERT INTO payroll_details (period_id, user_id, total_shifts, total_hours, hourly_rate, gross_amount, deduction, bonus, net_amount, late_count, early_leave_count, missing_checkout_count, note)
    SELECT 
        v_period2_id, 
        u.id, 
        COUNT(c.id), 
        ROUND(COALESCE(SUM(c.worked_minutes), 0) / 60.0, 2), 
        COALESCE(p.hourly_rate, 25000.00),
        ROUND(COALESCE(SUM(c.worked_minutes / 60.0 * COALESCE(p.hourly_rate, 25000.00) * s.salary_multiplier), 0.00), 2),
        0.00, -- deduction
        0.00, -- bonus
        ROUND(COALESCE(SUM(c.worked_minutes / 60.0 * COALESCE(p.hourly_rate, 25000.00) * s.salary_multiplier), 0.00), 2),
        SUM(CASE WHEN c.late_minutes > 5 THEN 1 ELSE 0 END),
        SUM(CASE WHEN c.checkout_at IS NOT NULL AND c.auto_checkout = false AND c.checkout_at < (s.date || ' ' || s.end_time || ' +07')::timestamptz - INTERVAL '5 minutes' THEN 1 ELSE 0 END),
        SUM(CASE WHEN c.auto_checkout = true OR c.checkout_at IS NULL THEN 1 ELSE 0 END),
        'Dữ liệu điểm danh kết chuyển tự động tháng 5'
    FROM users u
    JOIN employee_profiles p ON u.id = p.user_id
    JOIN check_in_records c ON u.id = c.user_id
    JOIN shifts s ON c.shift_id = s.id
    WHERE s.store_id = v_store1_id AND s.date >= '2026-05-01' AND s.date <= '2026-05-31' AND u.id IN (v_emp1_id, v_emp2_id, v_emp3_id, v_emp4_id, v_emp5_id, v_emp6_id, v_emp7_id)
    GROUP BY u.id, p.hourly_rate;

    RAISE NOTICE '=== HOÀN THÀNH TẠO DỮ LIỆU THÀNH CÔNG CHO 1 CỬA HÀNG ===';
END $$;
