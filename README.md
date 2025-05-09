# Hệ Thống Quản Lý Vào Ra Dựa Trên Nhận Diện Sinh Trắc Học

## Giới thiệu

Dự án tập trung vào việc xây dựng **hệ thống kiểm soát truy cập cho các phòng trong tòa nhà**, sử dụng thiết bị nền tảng là **Raspberry Pi 4** tích hợp camera, cảm biến và khóa điện tử. Hệ thống hỗ trợ **nhận diện khuôn mặt** và **xác thực vân tay**, nhằm tăng cường bảo mật và đem lại sự tiện lợi trong việc quản lý vào ra.

Hệ thống bao gồm **ba thành phần chính** phối hợp chặt chẽ với nhau để đảm bảo quy trình **đăng ký, nhận diện và quản lý truy cập** diễn ra hiệu quả.

---

## Cấu Trúc Hệ Thống

### 1. Thiết Bị Đăng Ký Sinh Trắc Học

- Thu thập dữ liệu sinh trắc học từ người dùng:
  - **Khuôn mặt** (qua camera chất lượng cao).
  - **Dấu vân tay** (qua cảm biến AS608).
- Dữ liệu sau khi thu thập được **xử lý sơ bộ** và gửi lên server để lưu trữ.
- Phục vụ cho bước xác thực tại các thiết bị kiểm soát ra vào.

### 2. Thiết Bị Nhận Diện Sinh Trắc Học

- Lắp đặt tại **các cửa ra vào** trong tòa nhà.
- Chức năng chính:
  - Nhận diện khuôn mặt.
  - Xác thực dấu vân tay.
  - So khớp dữ liệu sinh trắc với cơ sở dữ liệu từ server.
  - Điều khiển **khóa điện tử** để mở cửa khi xác thực thành công.
  - Gửi **thông tin truy cập** về server.
- Tích hợp chức năng:
  - **Giám sát trạng thái cửa**.
  - Phát hiện bất thường như: mở cửa quá thời gian, rung lắc thiết bị.

### 3. Server Trung Tâm

- Đóng vai trò là **bộ não điều phối** của hệ thống.
- Chức năng chính:
  - Lưu trữ toàn bộ dữ liệu sinh trắc và sự kiện truy cập.
  - Giao tiếp với các thiết bị qua giao thức **MQTT**.
  - Gửi lệnh điều khiển (mở cửa, cập nhật người dùng).
  - Hỗ trợ giám sát **realtime**, phân tích **lịch sử truy cập**.
  - Đảm bảo **tính toàn vẹn dữ liệu** và an ninh hệ thống.

---

## Công Nghệ Sử Dụng

- **Raspberry Pi 4**, Camera Module, Cảm biến vân tay AS608, khóa điện từ.
- **Python**, **FastAPI**, **MQTT (EMQX)**.
- **Node.js backend**, **MongoDB / PostgreSQL**.
- **MQTT protocol** để truyền thông giữa thiết bị và server.
- **Nhận diện khuôn mặt** sử dụng mô hình AI (ví dụ: InsightFace).
- **Giao diện người dùng** để đăng ký sinh trắc học và giám sát trạng thái hệ thống.

---
## Server Test
- docker run -d --name emqx -p 1883:1883 -p 8083:8083 -p 8084:8084 -p 8883:8883 -p 18083:18083 emqx/emqx-enterprise:latest
- http://localhost:18083/
- uvicorn server:app --host 0.0.0.0 --port 8080

## Mục Tiêu & Ý Nghĩa

Dự án không chỉ giúp sinh viên:

- Củng cố kiến thức về **Internet of Things**, **xử lý ảnh**, và **giao tiếp MQTT**.
- Rèn luyện kỹ năng thiết kế và triển khai hệ thống **thực tế từ phần cứng đến phần mềm**.
- Phát triển khả năng tích hợp và ứng dụng các giải pháp **AI và IoT** vào đời sống.

---

## Tác Giả

> Sinh viên thực hiện trong khuôn khổ đồ án tốt nghiệp tại [Tên Trường / Khoa].

